"""Offline checks for actions/whatsapp_reader.py: fake UIA trees shaped like WhatsApp Desktop's,
no WhatsApp window needed.

Run:  python -m unittest tests.test_whatsapp_reader
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import whatsapp_reader as wr
from actions.whatsapp_reader import Node as N


def row(name_parts, when, preview, unread=0, prefix_button=None, badge=""):
    """A chat-list row as WhatsApp nests it: row > copy > group > group > [header, preview, badges]."""
    header_kids = []
    if unread:
        header_kids.append(N("Text", f"{unread} mensajes no leídos"))
    header_kids.append(N("Group", kids=name_parts))
    if when:
        header_kids.append(N("Group", kids=[N("Text", when)]))
    label = " ".join(p.name for p in name_parts)
    cells = [N("Button", prefix_button) if prefix_button else N("Group"),
             N("DataItem", f"{label} {when}", header_kids),
             N("Group", kids=preview),
             N("DataItem", badge, [N("Group", badge)] if badge else [])]
    full = " ".join(x for x in (f"{unread} mensajes no leídos" if unread else "", prefix_button or "", label, when, badge) if x)
    inner = N("DataItem", full, [N("Group", kids=[N("Group", kids=cells)])])
    return N("DataItem", full, [inner])


def chat_list():
    return N("DataGrid", "Lista de chats", [
        row([N("Text", "MiAmor"), N("Image", "💕", cls="emoji")], "1:23 p. m.",
            [N("Text", "Esta fue la que se hizo Valeria")], unread=2, badge="Chat destacado"),
        row([N("Text", "Restaurante La 18")], "12:14 p. m.", [N("Text", "Ok")], prefix_button="Ver estado"),
        row([N("Text", "Vínculo laboral")], "Ayer",
            [N("Text", "~Etapa Productiva"), N("Text", ":\xa0"), N("Text", "Un placer atenderlos")]),
        row([N("Text", "Vínculo laboral")], "Ayer", [N("Text", "duplicado")]),
    ])


def bubble(sender_label=None, sender_button=None, parts=(), when="", status=None):
    kids = []
    if sender_label:
        kids.append(N("Group", f"{sender_label}:"))
    if sender_button:
        kids.append(N("Button", sender_button, [N("Text", sender_button)]))
    kids.append(N("Group", kids=list(parts)))
    t = N("Text", when)
    kids.append(N("Group", kids=[N("Button", f"{when} {status}", [t, N("Group", f" {status} ", [N("Image", "wds-ic-read")])])]
                                if status else [t]))
    return N("DataItem", kids=[N("Group", kids=[N("Group", kids=[N("Group", kids=kids)])])])


def conversation(items, title="Restaurante La 18", header_text=True):
    head = N("Button", f"{title} haz clic aquí para ver la información de contacto",
             [N("Text", title)] if header_text else [])
    return N("Group", "", [
        N("Group", kids=[N("Button", "Información del perfil"), head, N("Button", "Llamada")]),
        N("Group", kids=[N("Group", kids=[N("Text", "Usa WhatsApp en tu teléfono para ver mensajes anteriores")]),
                         N("Group", kids=items)]),
    ])


def day(label):
    return N("Group", kids=[N("Group", kids=[N("Text", label)])])


class CleanAndMatch(unittest.TestCase):
    def test_clean_strips_list_prefixes(self):
        self.assertEqual(wr.clean("2 mensajes no leídos MiAmor 💕 Chat destacado"), "MiAmor 💕")
        self.assertEqual(wr.clean("Ver estado Restaurante La 18"), "Restaurante La 18")
        self.assertEqual(wr.clean("~Etapa Productiva :\xa0 Un placer"), "Etapa Productiva: Un placer")

    def test_fuzzy_names(self):
        self.assertEqual(wr.score("mi amor", "MiAmor 💕"), 1.0)
        self.assertEqual(wr.score("restaurante la 18", "Restaurante La 18"), 1.0)
        self.assertGreater(wr.score("mama", "Mamá"), 0.99)
        self.assertLess(wr.score("trabajo", "MiAmor 💕"), 0.6)

    def test_pick_exact_wins_and_ambiguity_is_reported(self):
        best, rivals = wr.pick([{"name": "Mamá De Luis"}, {"name": "Mamá"}, {"name": "Mamá Cami"}], "mamá")
        self.assertEqual((best["name"], rivals), ("Mamá", []))
        best, rivals = wr.pick([{"name": "Mamá De Luis"}, {"name": "Mamá Cami"}], "mamá")
        self.assertEqual(len(rivals), 1)
        self.assertEqual(wr.pick([{"name": "Carlos"}], "restaurante"), (None, []))


class ChatList(unittest.TestCase):
    def test_rows_are_parsed_once_with_unread_and_clean_text(self):
        rows = wr.parse_list(chat_list())
        self.assertEqual([(r["name"], r["time"]) for r in rows],
                         [("MiAmor 💕", "1:23 p. m."), ("Restaurante La 18", "12:14 p. m."), ("Vínculo laboral", "Ayer")])
        self.assertEqual(rows[0]["unread"], 2)
        self.assertEqual(rows[0]["preview"], "Esta fue la que se hizo Valeria")
        self.assertEqual(rows[1]["unread"], 0)
        self.assertEqual(rows[2]["preview"], "Etapa Productiva: Un placer atenderlos")

    def test_formats(self):
        rows = wr.parse_list(chat_list())
        unread = wr.format_unread(rows)
        self.assertIn("MiAmor 💕 (2 sin leer, 1:23 p. m.): Esta fue la que se hizo Valeria", unread)
        self.assertNotIn("Restaurante", unread)
        self.assertIn("No hay chats sin leer", wr.format_unread(rows[1:]))
        recent = wr.format_recent(rows, 2)
        self.assertEqual(recent.count("\n- "), 2)
        self.assertNotIn("Chat destacado", recent)

    def test_search_results_skip_message_hits(self):
        grid = N("DataGrid", "Resultados de la búsqueda.", [
            N("DataItem", "Chats", [N("Text", "Chats", [N("Text", "Chats")])]),
            row([N("Text", "Mamá")], "Ayer", [N("Text", "sipi")]),
            N("DataItem", "Mensajes", [N("Text", "Mensajes", [N("Text", "Mensajes")])]),
            row([N("Text", "Brenda Cárdenas")], "10/9/2026", [N("Text", "Hola mami")]),
        ])
        self.assertEqual([r["name"] for r in wr.parse_list(grid, chats_only=True)], ["Mamá"])


class Conversation(unittest.TestCase):
    def test_direct_chat_full_text_sender_time_and_day(self):
        long_text = "Menú del día: " + "arroz, " * 80 + "fin"
        main = conversation([
            day("Ayer"),
            N("Group", kids=[bubble("Restaurante La 18", parts=[N("Text", long_text)], when="11:44 a. m.")]),
            day("Hoy"),
            N("Group", kids=[bubble("Tú", parts=[N("Button", "Abrir foto")], when="12:06 p. m.", status="Leído")]),
            N("Group", kids=[bubble("Restaurante La 18", parts=[N("Text", "Ok"), N("Image", "🙏", cls="b93 emoji")],
                                    when="12:14 p. m.")]),
        ])
        title, msgs = wr.parse_messages(main)
        self.assertEqual(title, "Restaurante La 18")
        self.assertEqual(msgs[0], {"sender": "Restaurante La 18", "day": "Ayer", "time": "11:44 a. m.", "text": long_text})
        self.assertEqual((msgs[1]["sender"], msgs[1]["text"], msgs[1]["day"]), ("Tú", "[foto]", "Hoy"))
        self.assertEqual(msgs[2]["text"], "Ok🙏")
        out = wr.format_messages(title, msgs, 2)
        self.assertIn("[Hoy 12:14 p. m.] Restaurante La 18: Ok🙏", out)
        self.assertNotIn("arroz", out)          # only the last 2
        self.assertIn(long_text, wr.format_messages(title, msgs, 10))   # never truncated

    def test_group_chat_runs_take_sender_from_avatar(self):
        run = N("Group", kids=[
            N("Group", kids=[N("Button", "Abrir los detalles del chat para Pablo Romero Wposs")]),
            bubble(sender_button="Pablo Romero Wposs", parts=[N("Text", "La vuelta de curiti es la vía")], when="4:57 p. m."),
            bubble(parts=[N("Text", "Y pescadero le da miedo a Carlos")], when="4:57 p. m."),
        ])
        title, msgs = wr.parse_messages(conversation([run], title="Acampar", header_text=False))
        self.assertEqual(title, "Acampar")
        self.assertEqual([(m["sender"], m["text"]) for m in msgs],
                         [("Pablo Romero Wposs", "La vuelta de curiti es la vía"),
                          ("Pablo Romero Wposs", "Y pescadero le da miedo a Carlos")])


class Watcher(unittest.TestCase):
    def setUp(self):
        wr._incoming.clear()
        wr._last_rows.clear()

    def test_new_incoming_messages_are_cached(self):
        base = [{"name": "Mamá", "time": "9:00 a. m.", "preview": "hola", "unread": 0}]
        wr._watcher_seed(base)                         # first look: baseline only
        self.assertEqual(len(wr._incoming), 0)
        wr._watcher_seed([{"name": "Mamá", "time": "9:05 a. m.", "preview": "ya llegué", "unread": 1}])
        wr._watcher_seed([{"name": "Mamá", "time": "9:05 a. m.", "preview": "ya llegué", "unread": 1}])
        self.assertEqual(len(wr._incoming), 1)
        out = wr.format_new("mama", [])
        self.assertIn("Mamá (9:05 a. m.", out)
        self.assertIn("ya llegué", out)
        self.assertIn("Ningún mensaje nuevo", wr.format_new("carlos", []))


class EntryPoint(unittest.TestCase):
    def test_unknown_action_and_other_os(self):
        self.assertTrue(wr.whatsapp_read({"action": "send"}).startswith("Unknown whatsapp_read action"))
        with mock.patch("platform.system", return_value="Linux"):
            self.assertTrue(wr.whatsapp_read({"action": "unread"}).startswith("No se pudo"))

    def test_tool_is_declared_and_routed(self):
        main = (Path(wr.__file__).parent.parent / "main.py").read_text(encoding="utf-8")
        self.assertIn('"name": "whatsapp_read"', main)
        self.assertIn('elif name == "whatsapp_read"', main)
        prompt = (Path(wr.__file__).parent.parent / "core" / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn("whatsapp_read", prompt)


if __name__ == "__main__":
    unittest.main()
