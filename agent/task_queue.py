import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any

from memory.memory_manager import BASE_DIR

# Goals survive a restart: unfinished ones come back paused (never re-run on their own, they may
# have side effects) and resume from their saved turns, so finished steps are not repeated.
# One stopped in the middle of an action comes back blocked: that action may have happened.
TASKS_PATH    = BASE_DIR / "memory" / "tasks.json"
KEEP_FINISHED = 30


class TaskStatus(Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    PAUSED     = "paused"
    BLOCKED    = "blocked"     # waiting for the user's yes to a risky step
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


FINISHED = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class TaskPriority(Enum):
    LOW    = 3
    NORMAL = 2
    HIGH   = 1   


@dataclass(order=True)
class Task:
    priority:    int                       
    created_at:  float = field(compare=False)
    task_id:     str   = field(compare=False)
    goal:        str   = field(compare=False)
    status:      TaskStatus = field(compare=False, default=TaskStatus.PENDING)
    result:      Any        = field(compare=False, default=None)
    error:       str        = field(compare=False, default="")
    speak:       Any        = field(compare=False, default=None)   
    on_complete: Any        = field(compare=False, default=None)  
    cancel_flag: threading.Event = field(compare=False, default_factory=threading.Event)
    step:        str        = field(compare=False, default="")
    pause_flag:  threading.Event = field(compare=False, default_factory=threading.Event)
    state:       dict       = field(compare=False, default_factory=dict)   # executor's history, plan, outcome


_SAVED = ("priority", "created_at", "task_id", "goal", "result", "error", "step", "state")


class TaskQueue:
    def __init__(self, max_concurrent: int = 1, path=TASKS_PATH):
        self._queue:        list[Task]       = []
        self._lock:         threading.Lock   = threading.Lock()
        self._condition:    threading.Condition = threading.Condition(self._lock)
        self._tasks:        dict[str, Task]  = {} 
        self._running:      bool             = False
        self._worker_thread: threading.Thread | None = None
        self._max_concurrent = max_concurrent
        self._active_count   = 0
        self._executor       = None
        self.on_change: Callable | None = None   # (task_id, goal, step); goal "" = finished. Feeds the HUD.
        self._path           = path
        self._save_lock      = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as e:
            print(f"[TaskQueue] ⚠️ Saved tasks unreadable, starting empty: {e}")
            return
        for d in saved if isinstance(saved, list) else []:
            try:
                task = Task(**{k: d[k] for k in _SAVED if k in d}, status=TaskStatus(d["status"]))
            except Exception:
                continue
            if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                from agent.executor import interrupted
                call = interrupted(task.state)
                if call:   # stopped in the middle of an action: it may have happened, the user decides
                    task.status = TaskStatus.BLOCKED
                    task.step = f"INTERRUPTED while running {call[2:120]}; it may have happened. Check before resuming"
                elif task.state.get("approved"):   # a yes from before the restart is asked again, not assumed
                    task.state["awaiting"] = task.state.pop("approved")
                    task.status, task.step = TaskStatus.BLOCKED, "INTERRUPTED before the approved step ran; ask again"
                else:
                    task.status, task.step = TaskStatus.PAUSED, "INTERRUPTED by a restart; resume to continue"
            self._tasks[task.task_id] = task

    def _save(self) -> None:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.created_at)
            done = [t for t in tasks if t.status in FINISHED][-KEEP_FINISHED:]
            rows = [{**{k: getattr(t, k) for k in _SAVED}, "status": t.status.value}
                    for t in tasks if t.status not in FINISHED or t in done]
        with self._save_lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                tmp.replace(self._path)
            except Exception as e:
                print(f"[TaskQueue] ⚠️ Could not save tasks: {e}")

    def _notify(self, task: Task, step: str | None = None) -> None:
        if step is not None:
            task.step = step
        self._save()
        if self.on_change:
            done = task.status in FINISHED or task.status in (TaskStatus.PAUSED, TaskStatus.BLOCKED)
            try:
                self.on_change(task.task_id, "" if done else task.goal, task.step)
            except Exception as e:
                print(f"[TaskQueue] ⚠️ on_change error: {e}")

    def _get_executor(self):
        if self._executor is None:
            from agent.executor import AgentExecutor
            self._executor = AgentExecutor()
        return self._executor

    def start(self) -> None:
        if self._running:
            return
        self._running      = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="AgentTaskQueue"
        )
        self._worker_thread.start()
        print("[TaskQueue] ✅ Started")

    def stop(self) -> None:
        self._running = False
        with self._condition:
            self._condition.notify_all()
        print("[TaskQueue] 🔴 Stopped")

    def submit(
        self,
        goal:        str,
        priority:    TaskPriority = TaskPriority.NORMAL,
        speak:       Callable | None = None,
        on_complete: Callable | None = None,
    ) -> str:

        task_id = str(uuid.uuid4())[:8]
        task    = Task(
            priority    = priority.value,
            created_at  = time.time(),
            task_id     = task_id,
            goal        = goal,
            speak       = speak,
            on_complete = on_complete,
        )

        with self._condition:
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._tasks[task_id] = task
            self._condition.notify()

        print(f"[TaskQueue] 📥 Task queued: [{task_id}] {goal[:60]}")
        self._notify(task, "QUEUED")
        return task_id

    def cancel(self, task_id: str) -> bool:

        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in FINISHED:
                return False

            task.cancel_flag.set()
            task.status = TaskStatus.CANCELLED
            print(f"[TaskQueue] 🚫 Task cancelled: [{task_id}]")
        self._notify(task)
        return True

    def pause(self, task_id: str) -> bool:
        """A running task stops after its current action (saved, resumable); a queued one just waits."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return False
            task.pause_flag.set()
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.PAUSED
                if task in self._queue:
                    self._queue.remove(task)
        self._notify(task, "PAUSED")
        return True

    def resume(self, task_id: str, speak: Callable | None = None, on_complete: Callable | None = None,
               approve: bool = False) -> bool:
        """Queues a paused, blocked or failed task again; it continues from its saved turns.
        approve: the user said yes to the step a BLOCKED task stopped at, so that step runs."""
        with self._condition:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PAUSED, TaskStatus.BLOCKED, TaskStatus.FAILED):
                return False
            awaiting = task.state.pop("awaiting", None)   # a yes approves only the step asked about last
            task.state.pop("approved", None)
            if approve and task.status == TaskStatus.BLOCKED and awaiting:
                task.state["approved"] = awaiting
            task.status, task.error = TaskStatus.PENDING, ""
            task.pause_flag.clear()
            task.cancel_flag.clear()
            task.speak, task.on_complete = speak or task.speak, on_complete or task.on_complete
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._condition.notify()
        self._notify(task, "RESUMED")
        return True

    def get_status(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            history = task.state.get("history") or []
            return {
                "task_id": task.task_id,
                "goal":    task.goal,
                "status":  task.status.value,
                "outcome": task.state.get("outcome", ""),
                "plan":    task.state.get("plan", []),
                "result":  task.result,
                "error":   task.error,
                "step":    task.step,
                "turns":   len(history),
                "recent":  [h[:600] for h in history[-3:]],
            }

    def get_all_statuses(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "goal":    t.goal[:50],
                    "status":  t.status.value,
                    "outcome": t.state.get("outcome", ""),
                    "step":    t.step,
                }
                for t in self._tasks.values()
            ]

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._queue if t.status == TaskStatus.PENDING)

    def _worker_loop(self) -> None:
        while self._running:
            task = None

            with self._condition:
                while self._running and not self._next_task():
                    self._condition.wait(timeout=1.0)
                task = self._next_task()
                if task:
                    task.status = TaskStatus.RUNNING
                    self._active_count += 1
                    try:
                        self._queue.remove(task)
                    except ValueError:
                        pass

            if task:
                threading.Thread(
                    target=self._run_task,
                    args=(task,),
                    daemon=True,
                    name=f"AgentTask-{task.task_id}"
                ).start()

    def _next_task(self) -> Task | None:
        if self._active_count >= self._max_concurrent:
            return None
        for task in self._queue:
            if task.status == TaskStatus.PENDING and not task.cancel_flag.is_set():
                return task
        return None

    def _run_task(self, task: Task) -> None:
        from agent.executor import OUTCOMES, Paused
        print(f"[TaskQueue] ▶️ Running: [{task.task_id}] {task.goal[:60]}")
        self._notify(task, "PLANNING")
        report = None
        try:
            executor = self._get_executor()
            result   = executor.execute(
                goal        = task.goal,
                speak       = task.speak,
                cancel_flag = task.cancel_flag,
                on_step     = lambda s: self._notify(task, s),
                state       = task.state,
                pause_flag  = task.pause_flag,
            )
            outcome = task.state.get("outcome", "unverified")

            with self._lock:
                if task.cancel_flag.is_set():
                    task.status = TaskStatus.CANCELLED
                else:
                    task.status = TaskStatus.BLOCKED if outcome == "blocked" else \
                                  TaskStatus.FAILED if outcome == "failed" else TaskStatus.COMPLETED
                    task.result = result
                    report = f"[{OUTCOMES.get(outcome, outcome)}] {result}"
                self._active_count -= 1
            print(f"[TaskQueue] ✅ Finished: [{task.task_id}] {outcome}")

        except Paused:
            with self._lock:
                task.status = TaskStatus.PAUSED
                self._active_count -= 1
            print(f"[TaskQueue] ⏸️ Paused: [{task.task_id}]")

        except Exception as e:
            with self._lock:
                task.status = TaskStatus.FAILED
                task.error  = str(e)
                task.state["outcome"] = "failed"
                self._active_count -= 1
                report = f"[{OUTCOMES['failed']}] {e}"
            print(f"[TaskQueue] ❌ Failed: [{task.task_id}] {e}")

        # Failures reach the brain too: before, a crashed task was never reported.
        if report and task.on_complete:
            try:
                task.on_complete(task.task_id, report)
            except Exception as e:
                print(f"[TaskQueue] ⚠️ on_complete callback error: {e}")

        self._notify(task, "PAUSED" if task.status == TaskStatus.PAUSED else None)
        with self._condition:
            self._condition.notify()

_queue        = TaskQueue()
_queue_started = False
_queue_lock    = threading.Lock()


def get_queue() -> TaskQueue:
    global _queue_started
    with _queue_lock:
        if not _queue_started:
            _queue.start()
            _queue_started = True
    return _queue