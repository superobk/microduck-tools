"""Loopback HTTP must not become a public plaintext or control gateway."""
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("ssh_http",ROOT/"deploy/studio/studio-ssh-http.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class SSHHTTPTests(unittest.TestCase):
    def config(self):
        return dict(listen="127.0.0.1",port=8790,public_origin="http://127.0.0.1:8790",
                    transport="ssh-loopback-http",demo=False,
                    controls_enabled=False,camera_enabled=False)

    def test_real_read_only_display_is_allowed(self):
        self.assertIs(module.validate_config(self.config())["demo"],False)

    def test_remote_or_wildcard_bind_is_refused(self):
        for value in ("0.0.0.0","::","192.168.1.56","localhost"):
            with self.subTest(bind=value),self.assertRaises(ValueError):
                module.validate_config({**self.config(),"listen":value})

    def test_origin_must_match_exact_loopback_host_and_port(self):
        for value in ("http://192.168.1.56:8790","http://localhost:8790",
                      "http://127.0.0.1:8791","http://127.0.0.1:8790/path"):
            with self.subTest(origin=value),self.assertRaises(ValueError):
                module.validate_config({**self.config(),"public_origin":value})

    def test_demo_or_implicit_transport_cannot_silently_replace_real_feedback(self):
        for patch in (dict(demo=True),dict(transport="https"),dict(demo=0)):
            with self.subTest(patch=patch),self.assertRaises(ValueError):
                module.validate_config({**self.config(),**patch})

    def test_control_and_camera_switches_are_not_permitted(self):
        for patch in (dict(controls_enabled=True),dict(camera_enabled=True),
                      dict(controls_enabled=None)):
            with self.subTest(patch=patch),self.assertRaises(ValueError):
                module.validate_config({**self.config(),**patch})

    def test_invalid_or_boolean_port_is_refused(self):
        for value in (80,65536,True,"8790"):
            with self.subTest(port=value),self.assertRaises(ValueError):
                module.validate_config({**self.config(),"port":value})
