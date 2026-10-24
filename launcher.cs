// Jarvis.exe: starts jarvis.pyw with python in a hidden console.
// Not pythonw: without a console, every console tool Jarvis runs (nvidia-smi, powershell...)
// pops its own window. With a hidden console they inherit it and stay invisible.
// Works from the project folder (.venv) and from the installed layout (python\, app\).
// Build: csc -target:winexe -win32icon:jarvis.ico -r:System.Windows.Forms.dll -out:Jarvis.exe launcher.cs
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class Launcher {
    static string Find(string dir, params string[] names) {
        foreach (string n in names)
            if (File.Exists(Path.Combine(dir, n))) return Path.Combine(dir, n);
        return null;
    }

    static void Main(string[] args) {
        string dir = System.AppDomain.CurrentDomain.BaseDirectory;
        string py = Find(dir, @".venv\Scripts\python.exe", @"python\python.exe");
        string script = Find(dir, "jarvis.pyw", @"app\jarvis.pyw");
        if (py == null || script == null) {
            MessageBox.Show("No encuentro Python o jarvis.pyw en " + dir + "\nCrea el entorno .venv primero.", "JARVIS");
            return;
        }
        Process.Start(new ProcessStartInfo(py, "\"" + script + "\" " + string.Join(" ", args)) {
            WorkingDirectory = Path.GetDirectoryName(script), UseShellExecute = false, CreateNoWindow = true
        });
    }
}
