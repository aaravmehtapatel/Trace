"""The matching cases that broke on the real Everywhere portfolio, pinned so they stay fixed."""
from src.ingest.domains import verify
from src.match.normalize import normalize
from src.match.resolve import score_candidate, state_for

AUTO, REJECT = 0.92, 0.72


def state(*args, **kw):
    return state_for(score_candidate(*args, **kw)["score"], AUTO, REJECT)


def test_normalize_strips_forms_and_startup_suffixes():
    assert normalize("Électric AI, Inc.") == "electric"
    assert normalize("Moksa.AI INC.") == "moksa"


def test_exact_name_in_same_city_links():
    assert state("Particle Health", "New York", None, "Particle Health Inc.", ["New York, NY"]) == "AUTO_LINKED"


def test_borough_counts_as_same_metro():
    # Pair Eyewear files from Brooklyn; the portfolio table says New York.
    assert state("Pair Eyewear", "New York", "paireyewear.com", "Pair Eyewear, Inc.", ["Brooklyn, NY"]) == "AUTO_LINKED"


def test_exact_name_alone_goes_to_review():
    # Devo, Inc. (a US security company) is not Devo, the London point-of-sale startup.
    assert state("Devo", "London", "devo.co.uk", "Devo, Inc.", ["Cambridge, MA"]) == "PENDING_REVIEW"


def test_investment_vehicles_are_dropped():
    assert state("Contra", "New York", None, "CONTRA PARTNERS LP", ["New York, NY"]) == "REJECTED"
    assert state("Hark", "New York", None, "Hark Capital IV, LP", ["New York, NY"]) == "REJECTED"
    assert state("Cube", "New York", None, "TS CUBE I, LLC", ["New York, NY"]) == "REJECTED"


def test_legal_name_from_the_site_links_a_rebranded_filer():
    # Switchboard's footer says Arrissa Labs Inc.; its Form D is filed under that name.
    assert state("Switchboard", "Sydney", "goswitchboard.com", "Arrissa Labs Inc.", ["Wilmington, DE"],
                 legal_name="Arrissa Labs Inc") == "AUTO_LINKED"


def _probe(domain, title, body="", **kw):
    return {"domain": domain, "status": 200, "title": title, "body_sample": body, **kw}


def test_guessed_namesake_domain_needs_review():
    p = _probe("revive.com", "Revive Lawn Treatment | Pet-Safe Organic Lawn Fertilizer", "revive organic lawn")
    v = verify("Revive", p, AUTO, REJECT, description="Revive provides a turn-key virtual tailoring solution "
               "that helps fashion brands repair garments", guessed=True)
    assert v["state"] == "PENDING_REVIEW"


def test_parked_or_redirected_sites_are_not_linked():
    assert verify("Morty", {"domain": "morty.ai", "status": 200, "parked": True}, AUTO, REJECT)["state"] == "REJECTED"
    rebrand = _probe("mryum.com", "me&u", redirected_off_domain=True, final_host="meandu.com")
    assert verify("Mr Yum", rebrand, AUTO, REJECT)["state"] == "PENDING_REVIEW"


def test_redirect_to_own_new_domain_is_a_move():
    from src.score import moved_not_gone
    assert moved_not_gone("EdSights", {"final_host": "edsights.com"})
    assert moved_not_gone("PropHero", {"final_host": "prophero.com.au"})
    assert not moved_not_gone("Aerodome", {"final_host": "flocksafety.com"})
    assert not moved_not_gone("Fora", {"final_host": "fora.northeastern.edu"})
