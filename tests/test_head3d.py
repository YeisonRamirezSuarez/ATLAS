import unittest

import head3d


class FaceDataTests(unittest.TestCase):
    def test_face_npz_has_every_part_and_target(self):   # Head3D.qml's morph weights follow head3d.TARGETS
        z = head3d.data()
        for part in head3d.PARTS:
            P, T = z[f"{part}_pos"], z[f"{part}_tri"]
            self.assertEqual(P.shape, z[f"{part}_nrm"].shape)
            self.assertLess(int(T.max()), len(P))
        for name in head3d.TARGETS:
            self.assertIn(f"skin@{name}", z)
        self.assertIn("mouth@jawOpen", z)                  # the teeth and tongue follow the jaw


if __name__ == "__main__":
    unittest.main()
