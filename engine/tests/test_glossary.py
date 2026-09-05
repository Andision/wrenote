"""Glossary: pure prompt/pair builders + store + endpoint + backend hooks."""
from __future__ import annotations

from wrenote.core import glossary

ENTRIES = [
    {"term": "Kubernetes", "translation": "Kubernetes", "note": ""},
    {"term": "张伟", "translation": "Zhang Wei", "note": "PM"},
    {"term": "ACME", "translation": "", "note": ""},  # no translation
    {"term": "  ", "translation": "x"},  # blank term — ignored
]


def test_stt_initial_prompt_lists_terms():
    p = glossary.stt_initial_prompt(ENTRIES)
    assert p.startswith("Glossary: ")
    assert "Kubernetes" in p and "张伟" in p and "ACME" in p
    assert p.endswith(".")


def test_stt_initial_prompt_empty_when_no_terms():
    assert glossary.stt_initial_prompt([]) == ""
    assert glossary.stt_initial_prompt([{"term": ""}]) == ""


def test_stt_initial_prompt_capped():
    many = [{"term": f"term{i:04d}"} for i in range(500)]
    p = glossary.stt_initial_prompt(many, max_chars=80)
    assert len(p) <= 80
    assert "term0000" in p  # whole terms only, never a truncated word
    assert not p.rstrip(".").endswith(",")


def test_mt_pairs_only_with_translation():
    pairs = glossary.mt_pairs(ENTRIES)
    assert ("Kubernetes", "Kubernetes") in pairs
    assert ("张伟", "Zhang Wei") in pairs
    assert all(term != "ACME" for term, _ in pairs)  # ACME has no translation


def test_mt_glossary_text():
    assert glossary.mt_glossary_text([]) == ""
    txt = glossary.mt_glossary_text([("张伟", "Zhang Wei")])
    assert "张伟 → Zhang Wei" in txt


def test_apply_to_backends_drives_hooks():
    class FakeSTT:
        prompt = None

        def set_initial_prompt(self, p):
            self.prompt = p

    class FakeMT:
        pairs = None

        def set_glossary(self, pairs):
            self.pairs = pairs

    stt, mt = FakeSTT(), FakeMT()
    glossary.apply_to_backends(ENTRIES, stt=stt, translator=mt)
    assert stt.prompt and "Kubernetes" in stt.prompt
    assert ("张伟", "Zhang Wei") in mt.pairs


def test_glossary_endpoint_roundtrip(client):
    assert client.get("/v1/glossary").json() == {"glossary": []}
    r = client.put(
        "/v1/glossary",
        json={"glossary": [
            {"term": "Kubernetes", "translation": "Kubernetes"},
            {"term": "", "translation": "dropped"},  # blank term dropped
        ]},
    )
    assert r.status_code == 200
    saved = r.json()["glossary"]
    assert len(saved) == 1
    assert saved[0]["term"] == "Kubernetes" and saved[0]["id"]  # id assigned
    # persisted
    assert client.get("/v1/glossary").json()["glossary"][0]["term"] == "Kubernetes"


def test_glossary_put_rejects_non_list(client):
    assert client.put("/v1/glossary", json={"glossary": "nope"}).status_code == 400
