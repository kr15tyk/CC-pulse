import emailer


def test_auxiliary_feed_degradation_is_not_a_green_daily_heartbeat():
    health = {
        "csfc": {
            "status": "healthy",
            "auxiliary_status": "degraded",
            "detail": "auxiliary feed failures: DISA STIGs & APL News",
        },
        "niap": {"status": "healthy"},
    }

    degraded = emailer._degraded_source_health(health)

    assert set(degraded) == {"csfc"}
