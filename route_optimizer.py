from functools import lru_cache

from database import haversine


EXACT_ROUTE_LIMIT = 10


def _distance(a, b):
    return haversine(a["lat"], a["lng"], b["lat"], b["lng"])


def _held_karp_trip(stops, depot):
    if not stops:
        return [], 0.0

    count = len(stops)
    trip_nodes = [depot] + stops
    distances = [[_distance(src, dst) for dst in trip_nodes] for src in trip_nodes]

    @lru_cache(maxsize=None)
    def solve(mask, last_index):
        if mask == (1 << last_index):
            return distances[0][last_index], [last_index]

        best_cost = float("inf")
        best_path = []
        remaining_mask = mask ^ (1 << last_index)
        for prev_index in range(1, count + 1):
            if not (remaining_mask & (1 << prev_index)):
                continue
            prev_cost, prev_path = solve(remaining_mask, prev_index)
            total_cost = prev_cost + distances[prev_index][last_index]
            if total_cost < best_cost:
                best_cost = total_cost
                best_path = prev_path + [last_index]
        return best_cost, best_path

    full_mask = 0
    for idx in range(1, count + 1):
        full_mask |= 1 << idx

    best_cost = float("inf")
    best_path = []
    for last_index in range(1, count + 1):
        candidate_cost, candidate_path = solve(full_mask, last_index)
        candidate_cost += distances[last_index][0]
        if candidate_cost < best_cost:
            best_cost = candidate_cost
            best_path = candidate_path

    ordered_stops = [trip_nodes[index] for index in best_path]
    return ordered_stops, round(best_cost, 2)


def _nearest_neighbor_trip(stops, depot):
    remaining = stops[:]
    ordered = []
    current = depot
    total_distance = 0.0

    while remaining:
        next_stop = min(remaining, key=lambda stop: _distance(current, stop))
        total_distance += _distance(current, next_stop)
        ordered.append(next_stop)
        remaining.remove(next_stop)
        current = next_stop

    total_distance += _distance(current, depot)
    return ordered, round(total_distance, 2)


def _optimize_trip(stops, depot, force_exact=False):
    if force_exact or len(stops) <= 8:
        return _held_karp_trip(stops, depot)
    return _nearest_neighbor_trip(stops, depot)


def _build_savings_routes(stops, truck_capacity, depot):
    routes = {
        stop["id"]: {
            "stops": [stop["id"]],
            "load": stop["current_level"],
        }
        for stop in stops
    }
    node_to_route = {stop["id"]: stop["id"] for stop in stops}
    stop_map = {stop["id"]: stop for stop in stops}

    savings = []
    for i, stop_i in enumerate(stops):
        for stop_j in stops[i + 1 :]:
            saving = (
                _distance(depot, stop_i)
                + _distance(depot, stop_j)
                - _distance(stop_i, stop_j)
            )
            savings.append((saving, stop_i["id"], stop_j["id"]))

    savings.sort(reverse=True)

    for _, left_id, right_id in savings:
        left_route_id = node_to_route[left_id]
        right_route_id = node_to_route[right_id]
        if left_route_id == right_route_id:
            continue

        left_route = routes[left_route_id]
        right_route = routes[right_route_id]
        if left_route["load"] + right_route["load"] > truck_capacity:
            continue

        left_nodes = left_route["stops"]
        right_nodes = right_route["stops"]
        if left_id not in (left_nodes[0], left_nodes[-1]):
            continue
        if right_id not in (right_nodes[0], right_nodes[-1]):
            continue

        if left_nodes[-1] == left_id and right_nodes[0] == right_id:
            merged_nodes = left_nodes + right_nodes
        elif left_nodes[0] == left_id and right_nodes[-1] == right_id:
            merged_nodes = list(reversed(left_nodes)) + list(reversed(right_nodes))
        elif left_nodes[0] == left_id and right_nodes[0] == right_id:
            merged_nodes = list(reversed(left_nodes)) + right_nodes
        else:
            merged_nodes = left_nodes + list(reversed(right_nodes))

        routes[left_route_id] = {
            "stops": merged_nodes,
            "load": left_route["load"] + right_route["load"],
        }
        for node_id in merged_nodes:
            node_to_route[node_id] = left_route_id
        del routes[right_route_id]

    return [
        {
            "stops": [stop_map[node_id] for node_id in route["stops"]],
            "load": route["load"],
        }
        for route in routes.values()
    ]


def _exact_partition_routes(stops, truck_capacity, depot):
    if not stops:
        return [], "exact"

    stop_count = len(stops)
    if stop_count > EXACT_ROUTE_LIMIT:
        return None, "heuristic"

    stop_map = {index: stop for index, stop in enumerate(stops)}
    feasible_subsets = {}

    for subset_mask in range(1, 1 << stop_count):
        subset_stops = []
        load = 0.0
        for index in range(stop_count):
            if subset_mask & (1 << index):
                stop = stop_map[index]
                load += stop["current_level"]
                if load > truck_capacity:
                    break
                subset_stops.append(stop)
        if load > truck_capacity:
            continue

        ordered_stops, total_distance = _optimize_trip(subset_stops, depot, force_exact=True)
        feasible_subsets[subset_mask] = {
            "stops": ordered_stops,
            "load": round(load, 2),
            "total_distance": total_distance,
        }

    @lru_cache(maxsize=None)
    def solve(mask):
        if mask == 0:
            return 0.0, []

        first_bit = mask & -mask
        best_distance = float("inf")
        best_routes = []

        subset = mask
        while subset:
            if subset & first_bit and subset in feasible_subsets:
                remaining = mask ^ subset
                remaining_distance, remaining_routes = solve(remaining)
                candidate_distance = feasible_subsets[subset]["total_distance"] + remaining_distance
                if candidate_distance < best_distance:
                    best_distance = candidate_distance
                    best_routes = [feasible_subsets[subset]] + remaining_routes
            subset = (subset - 1) & mask

        return best_distance, best_routes

    _, routes = solve((1 << stop_count) - 1)
    return routes, "exact"


def _normalize_route(route, route_index, depot, trip_colors, over_capacity=False, over_by=0):
    ordered_stops = [dict(stop) for stop in route["stops"]]
    route_coordinates = [[depot["lat"], depot["lng"]]]
    for stop_index, stop in enumerate(ordered_stops, start=1):
        stop["route_sequence"] = stop_index
        route_coordinates.append([stop["lat"], stop["lng"]])
    route_coordinates.append([depot["lat"], depot["lng"]])

    return {
        "trip_id": f"trip-{route_index}",
        "name": f"Trip {route_index}",
        "stops": ordered_stops,
        "load": round(route["load"], 2),
        "remaining_capacity": round(max(route["truck_capacity"] - route["load"], 0), 2),
        "total_distance": route["total_distance"],
        "total_stops": len(ordered_stops),
        "color": trip_colors[(route_index - 1) % len(trip_colors)],
        "over_capacity": over_capacity,
        "over_by": round(over_by, 2),
        "route_coordinates": route_coordinates,
    }


def plan_collector_routes(bins, truck_capacity, depot):
    actionable = [dict(bin_) for bin_ in bins if bin_["current_level"] > 0]
    oversized = [bin_ for bin_ in actionable if bin_["current_level"] > truck_capacity]
    serviceable = [bin_ for bin_ in actionable if bin_["current_level"] <= truck_capacity]
    trip_colors = ["#1f7a4f", "#2563eb", "#d97706", "#dc2626", "#0f766e", "#7c3aed"]

    exact_routes, algorithm = _exact_partition_routes(serviceable, truck_capacity, depot)
    if exact_routes is None:
        base_routes = _build_savings_routes(serviceable, truck_capacity, depot)
        routes = []
        for route in base_routes:
            ordered_stops, total_distance = _optimize_trip(route["stops"], depot)
            routes.append(
                {
                    "stops": ordered_stops,
                    "load": round(route["load"], 2),
                    "total_distance": total_distance,
                    "truck_capacity": truck_capacity,
                }
            )
        algorithm = "heuristic"
    else:
        routes = [
            {
                "stops": route["stops"],
                "load": route["load"],
                "total_distance": route["total_distance"],
                "truck_capacity": truck_capacity,
            }
            for route in exact_routes
        ]

    trips = [
        _normalize_route(route, route_index, depot, trip_colors)
        for route_index, route in enumerate(routes, start=1)
    ]

    oversized_routes = []
    for oversized_index, stop in enumerate(
        sorted(oversized, key=lambda item: (-item["pct"], item["dist"])),
        start=len(trips) + 1,
    ):
        stop_copy = dict(stop)
        total_distance = round(_distance(depot, stop_copy) * 2, 2)
        oversized_routes.append(
            _normalize_route(
                {
                    "stops": [stop_copy],
                    "load": round(stop_copy["current_level"], 2),
                    "total_distance": total_distance,
                    "truck_capacity": truck_capacity,
                },
                oversized_index,
                depot,
                trip_colors,
                over_capacity=True,
                over_by=stop_copy["current_level"] - truck_capacity,
            )
        )

    trips.extend(oversized_routes)
    trips.sort(
        key=lambda trip: (
            trip["over_capacity"],
            trip["total_distance"],
            -trip["load"],
        )
    )

    flattened_route = []
    for trip_number, trip in enumerate(trips, start=1):
        trip["trip_number"] = trip_number
        trip["name"] = f"Trip {trip_number}"
        for stop in trip["stops"]:
            stop_copy = dict(stop)
            stop_copy["trip_number"] = trip_number
            flattened_route.append(stop_copy)

    return {
        "truck_capacity": truck_capacity,
        "trips": trips,
        "route_stops": flattened_route,
        "unplanned_bins": [dict(bin_) for bin_ in bins if bin_["current_level"] <= 0],
        "total_distance": round(sum(trip["total_distance"] for trip in trips), 2),
        "total_load": round(sum(trip["load"] for trip in trips), 2),
        "oversized_bins": len(oversized),
        "actionable_bins": len(actionable),
        "algorithm": algorithm,
        "exact_route_limit": EXACT_ROUTE_LIMIT,
    }
