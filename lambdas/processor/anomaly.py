"""Anomaly detection rules for fleet telemetry.

Three layers of detection, all cheap enough to run inside a Lambda:

1. Threshold rules   - hard limits (cargo too warm, speeding, low fuel...).
2. Statistical rule  - z-score: is this reading far away from the
                       vehicle's own recent readings?
3. Predictive rule   - fits a straight line (least squares) to recent
                       cargo temperatures and forecasts the value a few
                       minutes ahead. If the forecast crosses the limit,
                       we warn *before* the cargo is actually spoiled.

Pure Python, no external libraries, so it is easy to unit test.
"""
from dataclasses import dataclass, field
import math

REQUIRED_FIELDS = {
    "vehicleId": str,
    "ts": int,
    "lat": (int, float),
    "lon": (int, float),
    "speedKmh": (int, float),
    "cargoTempC": (int, float),
    "engineTempC": (int, float),
    "fuelPct": (int, float),
}


@dataclass
class Thresholds:
    cargo_temp_max: float = 8.0       # cold-chain upper limit (deg C)
    cargo_temp_min: float = -25.0     # freezer lower limit (deg C)
    engine_temp_max: float = 110.0    # overheating (deg C)
    speed_max: float = 90.0           # km/h
    fuel_min: float = 10.0            # percent
    zscore_limit: float = 3.5
    forecast_minutes: float = 10.0
    min_history_for_stats: int = 5
    min_trend_r2: float = 0.8         # how "straight" the rise must be to trust it
    min_trend_span_sec: float = 20.0  # need at least this much history for a trend
    max_horizon_factor: float = 5.0   # never forecast further than 5x the data span
    min_trend_rate: float = 0.5       # deg C per minute - slower drifts are ignored


@dataclass
class Anomaly:
    code: str
    severity: str  # "warning" | "critical"
    message: str
    value: float = 0.0

    def to_dict(self):
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "value": round(float(self.value), 2),
        }


@dataclass
class Result:
    anomalies: list = field(default_factory=list)
    forecast_cargo_temp: float = None
    forecast_horizon_sec: int = None

    @property
    def has_critical(self):
        return any(a.severity == "critical" for a in self.anomalies)


def validate(frame: dict) -> list:
    """Return a list of problems with the frame (empty list = valid)."""
    problems = []
    for name, kind in REQUIRED_FIELDS.items():
        if name not in frame:
            problems.append(f"missing field '{name}'")
        elif isinstance(frame[name], bool) or not isinstance(frame[name], kind):
            problems.append(f"field '{name}' has wrong type")
    if problems:
        return problems
    if not -90 <= frame["lat"] <= 90 or not -180 <= frame["lon"] <= 180:
        problems.append("coordinates out of range")
    if not 0 <= frame["fuelPct"] <= 100:
        problems.append("fuelPct out of range")
    if frame["speedKmh"] < 0:
        problems.append("negative speed")
    if not 1 <= len(frame["vehicleId"]) <= 32:
        problems.append("bad vehicleId length")
    return problems


def zscore(value: float, history: list) -> float:
    if len(history) < 2:
        return 0.0
    mean = sum(history) / len(history)
    var = sum((x - mean) ** 2 for x in history) / (len(history) - 1)
    std = math.sqrt(var)
    if std < 0.25:  # readings almost constant; avoid huge z for tiny noise
        std = 0.25
    return (value - mean) / std


def linear_fit(points: list):
    """Least-squares line through points = [(ts_ms, value), ...].
    Returns (slope_per_sec, intercept, r2, xs) or None."""
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(t - t0) / 1000.0 for t, _ in points]
    ys = [v for _, v in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, r2, xs


def linear_forecast(points: list, ahead_ms: float):
    """Forecast the value ahead_ms after the last point (None if not enough data)."""
    fit = linear_fit(points)
    if fit is None:
        return None
    slope, intercept, _, xs = fit
    return intercept + slope * (xs[-1] + ahead_ms / 1000.0)


def detect(frame: dict, history: list, th: Thresholds = None) -> Result:
    """Run all rules.

    frame   - the new (already validated) reading
    history - older readings for the same vehicle, oldest first
              (dicts with at least "ts" and "cargoTempC")
    """
    th = th or Thresholds()
    res = Result()
    temp = float(frame["cargoTempC"])

    # 1. Threshold rules
    if temp > th.cargo_temp_max:
        res.anomalies.append(Anomaly(
            "CARGO_TEMP_HIGH", "critical",
            f"Cargo temperature {temp:.1f}C is above {th.cargo_temp_max}C", temp))
    if temp < th.cargo_temp_min:
        res.anomalies.append(Anomaly(
            "CARGO_TEMP_LOW", "warning",
            f"Cargo temperature {temp:.1f}C is below {th.cargo_temp_min}C", temp))
    eng = float(frame["engineTempC"])
    if eng > th.engine_temp_max:
        res.anomalies.append(Anomaly(
            "ENGINE_OVERHEAT", "critical",
            f"Engine temperature {eng:.0f}C is above {th.engine_temp_max}C", eng))
    spd = float(frame["speedKmh"])
    if spd > th.speed_max:
        res.anomalies.append(Anomaly(
            "OVERSPEED", "warning",
            f"Speed {spd:.0f} km/h is above {th.speed_max} km/h", spd))
    fuel = float(frame["fuelPct"])
    if fuel < th.fuel_min:
        res.anomalies.append(Anomaly(
            "LOW_FUEL", "warning", f"Fuel is low ({fuel:.0f}%)", fuel))
    if frame.get("doorOpen") and spd > 5:
        res.anomalies.append(Anomaly(
            "DOOR_OPEN_MOVING", "critical",
            "Cargo door is open while the vehicle is moving", spd))

    # 2. Statistical rule (sudden spike vs own recent behaviour)
    temps = [float(h["cargoTempC"]) for h in history]
    if len(temps) >= th.min_history_for_stats:
        z = zscore(temp, temps)
        if abs(z) >= th.zscore_limit:
            res.anomalies.append(Anomaly(
                "TEMP_SPIKE", "warning",
                f"Sudden cargo temperature change (z-score {z:.1f})", z))

    # 3. Predictive rule (only if not already over the limit)
    if len(temps) >= th.min_history_for_stats - 1:
        pts = [(int(h["ts"]), float(h["cargoTempC"])) for h in history]
        pts.append((int(frame["ts"]), temp))
        fit = linear_fit(pts)
        if fit is not None:
            slope, intercept, r2, xs = fit
            span = xs[-1]
            # Short history = short, honest forecast horizon
            horizon = min(th.forecast_minutes * 60, th.max_horizon_factor * span)
            forecast = intercept + slope * (span + horizon)
            # Only trust a clear, steady trend - random sensor noise would
            # otherwise be stretched into false alarms. No trend = "stable".
            trusted = r2 >= th.min_trend_r2 and span >= th.min_trend_span_sec \
                and abs(slope) * 60 >= th.min_trend_rate
            if trusted:
                res.forecast_cargo_temp = round(forecast, 2)
                res.forecast_horizon_sec = round(horizon)
            if trusted and slope > 0 and temp <= th.cargo_temp_max < forecast:
                res.anomalies.append(Anomaly(
                    "PREDICTED_TEMP_BREACH", "warning",
                    f"Cargo may reach {forecast:.1f}C within "
                    f"{max(1, round(horizon / 60))} min (limit {th.cargo_temp_max}C)",
                    forecast))
    return res
