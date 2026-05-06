import unittest

from lidaclips.navidrome_client import NavidromeClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}, timeout))
        if url.endswith("/rest/search3.view"):
            return FakeResponse(
                {
                    "subsonic-response": {
                        "status": "ok",
                        "searchResult3": {
                            "song": [
                                {
                                    "id": "nav-song-42",
                                    "artist": "The Example Band",
                                    "album": "Neon Nights",
                                    "title": "Bright Lights",
                                }
                            ]
                        },
                    }
                }
            )
        if url.endswith("/rest/getSong.view"):
            return FakeResponse({"subsonic-response": {"status": "ok", "song": {"id": params["id"]}}})
        raise AssertionError(f"unexpected URL {url}")


class NavidromeClientTests(unittest.TestCase):
    def test_finds_matching_song_id_with_subsonic_search(self):
        session = FakeSession()
        client = NavidromeClient("https://music.example", "user", "pass", session=session)

        song_id = client.find_song_id("The Example Band", "Neon Nights", "Bright Lights")

        self.assertEqual(song_id, "nav-song-42")
        params = session.calls[0][1]
        self.assertEqual(params["u"], "user")
        self.assertEqual(params["p"], "pass")
        self.assertEqual(params["query"], "Bright Lights")
        self.assertEqual(params["f"], "json")

    def test_checks_song_presence(self):
        client = NavidromeClient("https://music.example", "user", "pass", session=FakeSession())

        self.assertTrue(client.is_song_present("nav-song-42"))


if __name__ == "__main__":
    unittest.main()
