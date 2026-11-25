from anomaly import Thresholds, detect, linear_forecast, validate, zscore


def frame(**kw):
    base = {"vehicleId": "V1", "ts": 1_700_000_000_000, "lat": 21.1, "lon": 72.8,
            "speedKmh": 60.0, "cargoTempC": 4.0, "engineTempC": 85.0, "fuelPct": 70.0, "doorOpen": False}
    base.update(kw)
    return base


def codes(res):
    return {a.code for a in res.anomalies}


def history(temps, start=1_700_000_000_000, step=2000):
    return [{"ts": start + i * step, "cargoTempC": t} for i, t in enumerate(temps)]


def test_normal_reading_has_no_anomalies():
    assert detect(frame(), history([4.0, 4.1, 3.9, 4.0, 4.1])).anomalies == []


def test_threshold_rules():
    res = detect(frame(cargoTempC=9.5, speedKmh=101, engineTempC=115, fuelPct=5), [])
    assert {"CARGO_TEMP_HIGH", "OVERSPEED", "ENGINE_OVERHEAT", "LOW_FUEL"} <= codes(res)
    assert res.has_critical


def test_door_open_while_moving():
    assert "DOOR_OPEN_MOVING" in codes(detect(frame(doorOpen=True), []))
    assert "DOOR_OPEN_MOVING" not in codes(detect(frame(doorOpen=True, speedKmh=0), []))


def test_zscore_spike():
    h = history([4.0, 4.1, 3.9, 4.0, 4.1, 3.95])
    assert "TEMP_SPIKE" in codes(detect(frame(cargoTempC=7.5, ts=h[-1]["ts"] + 2000), h))


def test_predictive_breach_before_limit():
    # temperature rising 0.15 C every 2 s, still below 8 C right now
    temps = [5.0 + 0.15 * i for i in range(10)]
    h = history(temps)
    now = frame(cargoTempC=temps[-1] + 0.15, ts=h[-1]["ts"] + 2000)
    res = detect(now, h)
    assert "PREDICTED_TEMP_BREACH" in codes(res)
    assert "CARGO_TEMP_HIGH" not in codes(res)
    assert res.forecast_cargo_temp > 8


def test_linear_forecast_math():
    pts = [(0, 1.0), (1000, 2.0), (2000, 3.0)]
    assert abs(linear_forecast(pts, 1000) - 4.0) < 1e-9
    assert linear_forecast(pts[:2], 1000) is None


def test_zscore_flat_history_is_safe():
    assert zscore(4.0, [4.0] * 5) == 0.0


def test_validate():
    assert validate(frame()) == []
    bad = frame(lat=200)
    assert "coordinates out of range" in validate(bad)
    missing = frame()
    del missing["ts"]
    assert any("ts" in p for p in validate(missing))
    assert validate(frame(speedKmh=True))  # bool is not a number here


def test_custom_thresholds():
    th = Thresholds(cargo_temp_max=5.0)
    assert "CARGO_TEMP_HIGH" in codes(detect(frame(cargoTempC=5.5), [], th))


def test_noisy_flat_history_does_not_predict_breach():
    import random
    rng = random.Random(1)
    for _ in range(200):
        temps = [4.0 + rng.uniform(-0.3, 0.3) for _ in range(20)]
        h = history(temps)
        res = detect(frame(cargoTempC=4.0 + rng.uniform(-0.3, 0.3), ts=h[-1]["ts"] + 2000), h)
        assert "PREDICTED_TEMP_BREACH" not in codes(res)
