"""Tests de l'anonymisation. Lancer avec : python -m pytest tests"""

import pytest

from src.anonymization.anonymizer import (
    anonymize_text,
    detect,
    find_regex_spans,
    load_nlp,
    make_pseudonym,
    pseudonymize_text,
)

KEY = b"cle-de-test"


@pytest.fixture(scope="module")
def nlp():
    return load_nlp()


def anon(text, nlp=None):
    doc = nlp(text) if nlp else None
    return anonymize_text(text, detect(text, doc))


def test_email_url_ip_username():
    text = "mail bob@example.com, site https://foo.org/x, ip 192.168.0.12, ping @john_doe"
    assert anon(text) == "mail [EMAIL], site [URL], ip [IP], ping [USERNAME]"


def test_phone_detected_but_not_dates_or_years():
    assert anon("call +33 6 12 34 56 78 now") == "call [PHONE] now"
    assert anon("on 12-05-2010 and in 2005 2006") == "on 12-05-2010 and in 2005 2006"


def test_email_wins_over_username():
    # "@example" ne doit pas être traité comme pseudo à l'intérieur d'un email
    assert anon("write to bob@example.com") == "write to [EMAIL]"


def test_ner_person_and_location(nlp):
    out = anon("Barack Obama visited Paris last week.", nlp)
    assert "Barack Obama" not in out and "Paris" not in out
    assert "[PERSON]" in out and "[LOCATION]" in out


def test_norp_is_kept(nlp):
    # Les nationalités/religions ne sont pas remplacées (signal utile à la toxicité).
    assert "French" in anon("The French are here.", nlp)


def test_pseudonym_is_stable_and_case_insensitive():
    a = make_pseudonym("PERSON", "Barack Obama", KEY)
    assert a == make_pseudonym("PERSON", "  barack   OBAMA ", KEY)
    assert a != make_pseudonym("PERSON", "Barack Obama", b"autre-cle")
    assert a != make_pseudonym("LOCATION", "Barack Obama", KEY)


def test_pseudonymize_fills_mapping_and_hides_value():
    text = "mail bob@example.com"
    mapping = {}
    out = pseudonymize_text(text, find_regex_spans(text), KEY, mapping)
    assert "bob@example.com" not in out
    pseudo = out.split()[-1]
    assert mapping[pseudo] == ("EMAIL", "bob@example.com")


def test_text_without_personal_data_is_unchanged(nlp):
    text = "You are an idiot!"
    assert anon(text, nlp) == text


def test_ip_is_not_swallowed_by_phone():
    assert anon("from 71.64.6.46  01 today").startswith("from [IP]")


def test_wikipedia_jargon_and_orgs_are_kept(nlp):
    out = anon("Wikipedia and the BBC follow NPOV.", nlp)
    assert "Wikipedia" in out and "BBC" in out and "NPOV" in out


def test_at_sign_inside_url_gives_a_single_url():
    assert anon("see http://a.org/x/me@b.com_1.html, ok") == "see [URL], ok"


def test_email_on_www_domain_stays_an_email():
    assert anon("write bob@www.example.com") == "write [EMAIL]"
