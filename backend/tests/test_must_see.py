"""Must-see places: the traveller names or pins places, and the plan always fits them in when they are open."""
from app.schemas.place import Place, PlaceKind
from app.schemas.common import DataSource, LatLng
from app.schemas.trip import MustSeeInput
from app.services.places_service import match_must_see


def _activities(data: dict) -> list[dict]:
    return [i for d in data["days"] for i in d["items"] if i["type"] == "activity"]


def test_must_see_places_are_planned_and_spread_over_the_days(client, base_request):
    # neither is in the plan without being asked for (see the default Jaipur plan)
    must = [{"name": "Patrika Gate", "location": {"lat": 26.8418, "lng": 75.8025}},
            {"name": "Galtaji Temple", "location": {"lat": 26.9170, "lng": 75.8586}}]
    r = client.post("/api/trips/preview", json={**base_request, "must_see": must})
    assert r.status_code == 200, r.text
    data = r.json()
    by_day = [[i["place"]["name"] for i in d["items"] if i["type"] == "activity"] for d in data["days"]]
    assert all(any(n in day for n in ("Patrika Gate", "Galtaji Temple")) for day in by_day), by_day
    for item in _activities(data):
        if item["place"]["name"] in ("Patrika Gate", "Galtaji Temple"):
            # matched to the city's own record (real hours and fees), and labelled as the traveller's choice
            assert not item["place"]["id"].startswith("must-see-")
            assert item["suitability"]["reasons"][0] == "On your must-see list"
    assert data["request"]["must_see"][0]["name"] == "Patrika Gate"


def test_a_place_the_data_does_not_know_is_planned_from_its_pin(client, base_request):
    must = [{"name": "Cousin's rooftop cafe", "location": {"lat": 26.8800, "lng": 75.7800}}]
    data = client.post("/api/trips/preview", json={**base_request, "must_see": must}).json()
    item = next(i for i in _activities(data) if i["place"]["name"] == "Cousin's rooftop cafe")
    assert item["place"]["id"] == "must-see-0" and item["place"]["source"] == "user"
    assert (item["place"]["location"]["lat"], item["place"]["location"]["lng"]) == (26.88, 75.78)
    # nothing is invented: the hours are an assumption and the plan says so
    assert any("Opening hours not published" in n for n in item["notes"])


def test_must_sees_that_cannot_fit_or_are_too_far_are_reported(client, base_request):
    names = {"Hawa Mahal": (26.9239, 75.8267), "Albert Hall Museum": (26.9116, 75.8195),
             "Jantar Mantar": (26.9248, 75.8246), "Gaitore ki Chhatriyan": (26.943, 75.827),
             "Galtaji Temple": (26.917, 75.8586), "Patrika Gate": (26.8418, 75.8025),
             "Anokhi Museum of Hand Printing": (26.9869, 75.8558)}
    must = [{"name": n, "location": {"lat": a, "lng": b}} for n, (a, b) in names.items()]
    must.append({"name": "India Gate", "location": {"lat": 28.6129, "lng": 77.2295}})   # in Delhi
    r = client.post("/api/trips/preview", json={**base_request, "num_days": 1, "pace": "relaxed", "must_see": must})
    assert r.status_code == 200, r.text
    data = r.json()
    planned = {i["place"]["name"] for i in _activities(data)}
    assert planned and planned <= set(names)   # a one-day plan holds only must-sees while some are left
    missed = [u for u in data["unscheduled"] if u["reason"].startswith("On your must-see list, but")]
    assert {u["place"]["name"] for u in missed} == set(names) - planned
    assert any(w.startswith("Left out India Gate") and "km from the centre of Jaipur" in w for w in data["warnings"])
    assert sum("is on your must-see list but" in w for w in data["warnings"]) == len(missed)


def test_must_see_input_limits(client, base_request):
    one = {"name": "Hawa Mahal", "location": {"lat": 26.9239, "lng": 75.8267}}
    assert client.post("/api/trips/preview", json={**base_request, "must_see": [one] * 11}).status_code == 422
    assert client.post("/api/trips/preview", json={**base_request, "must_see": [{**one, "x": 1}]}).status_code == 422
    bad = {**one, "location": {"lat": 95, "lng": 0}}
    assert client.post("/api/trips/preview", json={**base_request, "must_see": [bad]}).status_code == 422


def test_matching_prefers_a_shared_name_then_the_place_at_the_pin():
    def place(pid: str, name: str, lat: float, lng: float) -> Place:
        return Place(id=pid, name=name, kind=PlaceKind.ATTRACTION, location=LatLng(lat=lat, lng=lng),
                     source=DataSource.OSM)

    tower = place("a", "Torre de Belém", 38.6916, -9.2160)
    kiosk = place("b", "Kiosk", 38.6917, -9.2161)
    pool = [kiosk, tower]
    # the name matches (accents folded) about 150 m away: that wins over a nameless point nearer the pin
    assert match_must_see(MustSeeInput(name="Torre de Belem", location=LatLng(lat=38.6929, lng=-9.2158)), pool) is tower
    # a pin right on a place matches it whatever the label says
    assert match_must_see(MustSeeInput(name="12 Rua X", location=LatLng(lat=38.69172, lng=-9.21612)), pool) is kiosk
    # nothing known near the pin
    assert match_must_see(MustSeeInput(name="Torre de Belem", location=LatLng(lat=38.70, lng=-9.20)), pool) is None
