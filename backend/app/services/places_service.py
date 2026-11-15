"""Places service: turns user interests and food preferences into provider queries,
then de-duplicates the results. Discovery queries for famous attractions are
always included so the itinerary can suggest places outside the user's interests.
"""
from __future__ import annotations

import re

from app.core.errors import TripAuraError
from app.core.geo import haversine_km
from app.core.text import fold
from app.providers.base import CityContext, PlaceQuery, PlacesProvider
from app.schemas.common import DataSource
from app.schemas.place import Place, PlaceKind
from app.schemas.trip import MustSeeInput, TripRequest

MUST_SEE_NAMED_M = 250   # a known place this close whose name shares a word is the same place
MUST_SEE_NEAR_M = 60     # a point pinned this close to a known place is that place, whatever its label
MUST_SEE_MAX_KM = 60     # farther than this from the city centre is not a day out from the stay


def _words(name: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z0-9]+", fold(name)) if len(w) >= 4}


def match_must_see(item: MustSeeInput, pool: list[Place]) -> Place | None:
    """The known place the traveller meant: a nearby place sharing a word of its name, else one right at the pin."""
    words = _words(item.name)
    best: tuple[tuple[int, float], Place] | None = None
    for p in pool:
        d = haversine_km(item.location.lat, item.location.lng, p.location.lat, p.location.lng) * 1000
        if d > MUST_SEE_NAMED_M:
            continue
        named = bool(words & _words(p.name))
        if not named and d > MUST_SEE_NEAR_M:
            continue
        key = (0 if named else 1, d)
        if best is None or key < best[0]:
            best = (key, p)
    return best[1] if best else None

INTEREST_QUERIES = {
    "museums": "museums", "monuments": "historical monuments", "cultural_centres": "cultural centres",
    "nature": "nature spots", "parks": "parks and gardens", "shopping": "shopping areas",
    "architecture": "architectural landmarks", "photography": "best photography spots",
    "adventure": "adventure activities", "history": "historical sites", "religious_sites": "temples and religious sites",
    "art": "art galleries", "markets": "local markets", "wildlife": "wildlife sanctuaries and zoos",
    "nightlife": "nightlife",
}
DIET_QUERIES = {
    "vegetarian": "vegetarian restaurants", "vegan": "vegan restaurants", "jain": "jain food restaurants",
    "eggetarian": "restaurants with egg dishes", "non_vegetarian": "non vegetarian restaurants",
}
FOOD_CATEGORY_QUERIES = {
    "cafes": "cafes", "street_food": "street food", "local_cuisine": "local cuisine restaurants",
    "budget_restaurants": "cheap restaurants", "fine_dining": "fine dining restaurants",
}


class PlacesService:
    def __init__(self, provider: PlacesProvider):
        self._p = provider

    def _run(self, city: CityContext, queries: list[PlaceQuery]) -> list[Place]:
        seen: dict[str, Place] = {}
        for q in queries:
            for place in self._p.search(city, q):
                if haversine_km(city.center.lat, city.center.lng, place.location.lat, place.location.lng) > city.radius_km * 1.6:
                    continue
                existing = seen.get(place.id)
                if existing:
                    extra = [t for t in place.categories if t not in existing.categories]
                    if extra:  # reassign (never mutate): lists may be shared with the city cache
                        existing.categories = existing.categories + extra
                    existing.is_famous = existing.is_famous or place.is_famous
                else:
                    # Shallow copy: per-trip changes only reassign fields (never mutate shared lists or
                    # dicts in place), so the cached city data stays untouched. A deep copy of ~1,000
                    # places cost ~0.7 s per trip.
                    seen[place.id] = place.model_copy()
        return list(seen.values())

    @property
    def _local(self) -> bool:
        """Mock and open-data providers hold the whole city locally: filter by tag instead of text search."""
        return not getattr(self._p, "supports_text_search", True)

    def attraction_candidates(self, city: CityContext, req: TripRequest) -> list[Place]:
        cname = city.name.split(",")[0]
        if self._local:
            queries = [PlaceQuery(text="famous", tag="famous", kind="attraction", max_results=40)]
            queries += [PlaceQuery(text=i, tag=i, kind="attraction", max_results=60) for i in req.interests]
            queries += [PlaceQuery(text="parks", tag="parks", kind="attraction", max_results=25),
                        PlaceQuery(text="all", tag="all", kind="attraction", max_results=100)]
        else:
            queries = [PlaceQuery(text=f"top tourist attractions in {cname}", tag="famous", kind="attraction")]
            for interest in req.interests:
                phrase = INTEREST_QUERIES.get(interest, interest.replace("_", " "))
                queries.append(PlaceQuery(text=f"{phrase} in {cname}", tag=interest, kind="attraction"))
            queries.append(PlaceQuery(text=f"parks in {cname}", tag="parks", kind="attraction"))
        places = self._run(city, queries)
        return [p for p in places if p.kind in (PlaceKind.ATTRACTION, PlaceKind.PARK)]

    def must_see_places(self, city: CityContext, items: list[MustSeeInput]) -> tuple[list[Place], list[str]]:
        """The traveller's must-see places, matched to the city's data where possible (for real opening hours and
        fees). A place the data does not have is planned from its name and point alone, and says so."""
        if not items:
            return [], []
        warnings: list[str] = []
        local_pool: list[Place] | None = None
        if self._local:
            local_pool = self._p.search(city, PlaceQuery(text="all", tag="all", kind="attraction", max_results=100_000))
        out: list[Place] = []
        seen: set[str] = set()
        for i, item in enumerate(items):
            away = haversine_km(city.center.lat, city.center.lng, item.location.lat, item.location.lng)
            if away > MUST_SEE_MAX_KM:
                warnings.append(f"Left out {item.name} from your must-see list: it is {away:.0f} km from the centre "
                                f"of {city.name.split(',')[0]}, too far for a day plan from your stay.")
                continue
            pool = local_pool
            if pool is None:
                try:
                    pool = self._p.search(city, PlaceQuery(text=item.name, tag="must_see", kind="attraction",
                                                           max_results=5))
                except TripAuraError:
                    pool = []
            found = match_must_see(item, pool)
            if found is not None:
                place = found.model_copy()
            else:
                place = Place(id=f"must-see-{i}", name=item.name, kind=PlaceKind.ATTRACTION, location=item.location,
                              description="Added by you. The map data has no details for it, so its hours and price "
                                          "are assumed.", source=DataSource.USER)
            if place.id not in seen:
                seen.add(place.id)
                out.append(place)
        return out, warnings

    def food_candidates(self, city: CityContext, req: TripRequest, limit: int = 800) -> list[Place]:
        cname = city.name.split(",")[0]
        if self._local:
            queries = [PlaceQuery(text="all", tag="all", kind="food", max_results=limit)]
        else:
            queries = [PlaceQuery(text=f"best restaurants in {cname}", tag="restaurants", kind="food")]
            for d in req.food_preferences:
                if d in DIET_QUERIES:
                    queries.append(PlaceQuery(text=f"{DIET_QUERIES[d]} in {cname}", tag=d, kind="food"))
            for c in req.food_categories or ["local_cuisine", "cafes"]:
                phrase = FOOD_CATEGORY_QUERIES.get(c, c.replace("_", " "))
                queries.append(PlaceQuery(text=f"{phrase} in {cname}", tag=c, kind="food"))
        places = self._run(city, queries)
        return [p for p in places if p.kind in (PlaceKind.RESTAURANT, PlaceKind.CAFE)]
