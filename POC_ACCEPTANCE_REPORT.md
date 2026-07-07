# POC Acceptance Report

Generated UTC: 2026-06-20T15:22:01.848558+00:00

- Tests pass: see final results package.
- Ruff passes: see final results package.
- Live trading disabled: true.
- Live order endpoint present: false.
- Read-only command summary: {
  "research_status": {
    "status": "ok",
    "payload": {
      "db_counts": {
        "market_snapshots": 41,
        "weather_snapshots": 41,
        "model_predictions": 246,
        "official_outcomes": 1,
        "prediction_outcomes": 174,
        "paper_fills": 0,
        "paper_positions": 0,
        "opportunity_snapshots": 0
      },
      "series": "KXHIGHLAX",
      "station": "KLAX"
    }
  },
  "time_debug": {
    "status": "ok",
    "payload": {
      "station": "KLAX",
      "now_utc": "2026-06-20T15:21:45.915419+00:00",
      "local_wall_time": "2026-06-20T08:21:45.915419-07:00",
      "fixed_local_standard_time": "2026-06-20T07:21:45.915419-08:00",
      "market_date": "2026-06-20",
      "climate_day_start_utc": "2026-06-20T08:00:00+00:00",
      "climate_day_end_utc": "2026-06-21T08:00:00+00:00",
      "climate_day_start_local_wall": "2026-06-20T01:00:00-07:00",
      "climate_day_end_local_wall": "2026-06-21T01:00:00-07:00",
      "remaining_window_start_local": "2026-06-20T08:21:45.915419",
      "remaining_window_end_local": "2026-06-21T01:00:00"
    }
  },
  "weather_debug": {
    "status": "ok",
    "payload": "WeatherSnapshot(station_id='KLAX', timestamp_utc=datetime.datetime(2026, 6, 20, 15, 21, 47, 877769, tzinfo=datetime.timezone.utc), observed_high_so_far_f=62.959999999999994, latest_observation_utc=datetime.datetime(2026, 6, 20, 15, 5, tzinfo=datetime.timezone.utc), observation_count=95, model_future_high_f=69.31428571428572, model_details={'future_max_by_model': {'temperature_2m__gfs_seamless': 69.4, 'temperature_2m__gfs013': 69.2, 'temperature_2m__gfs_global': 69.2, 'temperature_2m__best_match': 69.4}, 'selected_future_high_f': 69.31428571428572, 'selected_model_components': [{'column': 'temperature_2m__gfs_seamless', 'model_id': 'gfs_seamless', 'future_high_f': 69.4, 'weight': 1.0}, {'column': 'temperature_2m__gfs013', 'model_id': 'gfs013', 'future_high_f': 69.2, 'weight': 0.75}, {'column': 'temperature_2m__gfs_global', 'model_id': 'gfs_global', 'future_high_f': 69.2, 'weight': 0.75}, {'column': 'temperature_2m__best_match', 'model_id': 'best_match', 'future_high_f': 69.4, 'weight': 1.0}], 'weights_used': {'gfs_seamless': 1.0, 'gfs013': 0.75, 'gfs_global': 0.75, 'best_match': 1.0}, 'successful_models': ['gfs_seamless', 'gfs013', 'gfs_global', 'best_match'], 'failed_models': {}, 'fallback_used': False, 'feature_summary': {'cloud_cover_low_max': 100.0, 'cloud_cover_low_mean': 16.8, 'shortwave_radiation_max': 1026.0, 'shortwave_radiation_mean': 502.8, 'direct_radiation_max': 929.0, 'direct_radiation_mean': 387.0, 'wind_speed_10m_max': 15.5, 'wind_speed_10m_mean': 10.139999999999999, 'wind_gusts_10m_max': 20.8, 'wind_gusts_10m_mean': 11.77777777777778, 'apparent_temperature_max': 72.7, 'apparent_temperature_mean': 65.35, 'wind_direction_10m_mean': 251.73333333333332}, 'failed_variable_requests': {}, 'raw_columns': ['time', 'temperature_2m__gfs_seamless', 'cloud_cover__gfs_seamless', 'cloud_cover_low__gfs_seamless', 'cloud_cover_mid__gfs_seamless', 'cloud_cover_high__gfs_seamless', 'shortwave_radiation__gfs_seamless', 'direct_radiation__gfs_seamless', 'diffuse_radiation__gfs_seamless', 'sunshine_duration__gfs_seamless', 'apparent_temperature__gfs_seamless', 'wind_speed_10m__gfs_seamless', 'wind_gusts_10m__gfs_seamless', 'wind_direction_10m__gfs_seamless', 'relative_humidity_2m__gfs_seamless', 'dew_point_2m__gfs_seamless', 'temperature_2m__gfs013', 'cloud_cover__gfs013', 'cloud_cover_low__gfs013', 'cloud_cover_mid__gfs013', 'cloud_cover_high__gfs013', 'shortwave_radiation__gfs013', 'direct_radiation__gfs013', 'diffuse_radiation__gfs013', 'sunshine_duration__gfs013', 'apparent_temperature__gfs013', 'wind_speed_10m__gfs013', 'wind_gusts_10m__gfs013', 'wind_direction_10m__gfs013', 'relative_humidity_2m__gfs013', 'dew_point_2m__gfs013', 'temperature_2m__gfs_global', 'cloud_cover__gfs_global', 'cloud_cover_low__gfs_global', 'cloud_cover_mid__gfs_global', 'cloud_cover_high__gfs_global', 'shortwave_radiation__gfs_global', 'direct_radiation__gfs_global', 'diffuse_radiation__gfs_global', 'sunshine_duration__gfs_global', 'apparent_temperature__gfs_global', 'wind_speed_10m__gfs_global', 'wind_gusts_10m__gfs_global', 'wind_direction_10m__gfs_global', 'relative_humidity_2m__gfs_global', 'dew_point_2m__gfs_global', 'temperature_2m__best_match', 'cloud_cover__best_match', 'cloud_cover_low__best_match', 'cloud_cover_mid__best_match', 'cloud_cover_high__best_match', 'shortwave_radiation__best_match', 'direct_radiation__best_match', 'diffuse_radiation__best_match', 'sunshine_duration__best_match', 'apparent_temperature__best_match', 'wind_speed_10m__best_match', 'wind_gusts_10m__best_match', 'wind_direction_10m__best_match', 'relative_humidity_2m__best_match', 'dew_point_2m__best_match']})"
  },
  "opportunities": {
    "status": "ok",
    "payload": {
      "rows": [
        {
          "ticker": "KXHIGHLAX-26JUN20-B71.5",
          "bracket": "Will the **high temp in LA** be 71-72\u00b0 on Jun 20, 2026?",
          "p_yes": 0.11425,
          "yes_bid": "0.6100",
          "yes_ask": "0.6200",
          "no_bid": "0.3800",
          "no_ask": "0.3900",
          "yes_edge": "-0.50575",
          "no_edge": "0.49575",
          "best_side": "no",
          "best_edge": "0.49575",
          "required_hurdle": "0.09",
          "would_trade": true,
          "reason": null
        },
        {
          "ticker": "KXHIGHLAX-26JUN20-B69.5",
          "bracket": "Will the **high temp in LA** be 69-70\u00b0 on Jun 20, 2026?",
          "p_yes": 0.67885,
          "yes_bid": "0.3100",
          "yes_ask": "0.3300",
          "no_bid": "0.6700",
          "no_ask": "0.6900",
          "yes_edge": "0.34885",
          "no_edge": "-0.36885",
          "best_side": "yes",
          "best_edge": "0.34885",
          "required_hurdle": "0.09",
          "would_trade": true,
          "reason": null
        },
        {
          "ticker": "KXHIGHLAX-26JUN20-B67.5",
          "bracket": "Will the **high temp in LA** be 67-68\u00b0 on Jun 20, 2026?",
          "p_yes": 0.2038,
          "yes_bid": null,
          "yes_ask": "0.0100",
          "no_bid": "0.9900",
          "no_ask": null,
          "yes_edge": "0.1938",
          "no_edge": null,
          "best_side": "yes",
          "best_edge": "0.1938",
          "required_hurdle": "0.09",
          "would_trade": true,
          "reason": null
        },
        {
          "ticker": "KXHIGHLAX-26JUN20-B73.5",
          "bracket": "Will the **high temp in LA** be 73-74\u00b0 on Jun 20, 2026?",
          "p_yes": 0.00065,
          "yes_bid": "0.0900",
          "yes_ask": "0.1000",
          "no_bid": "0.9000",
          "no_ask": "0.9100",
          "yes_edge": "-0.09935",
          "no_edge": "0.08935",
          "best_side": "no",
          "best_edge": "0.08935",
          "required_hurdle": "0.09",
          "would_trade": false,
          "reason": "best edge 0.08935 <= hurdle 0.09"
        },
        {
          "ticker": "KXHIGHLAX-26JUN20-T74",
          "bracket": "Will the **high temp in LA** be >74\u00b0 on Jun 20, 2026?",
          "p_yes": 0.0,
          "yes_bid": null,
          "yes_ask": "0.0100",
          "no_bid": "0.9900",
          "no_ask": null,
          "yes_edge": "-0.0100",
          "no_edge": null,
          "best_side": "yes",
          "best_edge": "-0.0100",
          "required_hurdle": "0.09",
          "would_trade": false,
          "reason": "best edge -0.0100 <= hurdle 0.09"
        },
        {
          "ticker": "KXHIGHLAX-26JUN20-T67",
          "bracket": "Will the **high temp in LA** be <67\u00b0 on Jun 20, 2026?",
          "p_yes": 0.00245,
          "yes_bid": null,
          "yes_ask": "0.0100",
          "no_bid": "0.9900",
          "no_ask": null,
          "yes_edge": "-0.00755",
          "no_edge": null,
          "best_side": "yes",
          "best_edge": "-0.00755",
          "required_hurdle": "0.09",
          "would_trade": false,
          "reason": "best edge -0.00755 <= hurdle 0.09"
        }
      ]
    }
  },
  "collect_once": {
    "status": "ok",
    "payload": {
      "stored_predictions": 6,
      "market_count": 6,
      "station": "KLAX",
      "market_date": "2026-06-20",
      "weather": "WeatherSnapshot(station_id='KLAX', timestamp_utc=datetime.datetime(2026, 6, 20, 15, 22, 1, 815298, tzinfo=datetime.timezone.utc), observed_high_so_far_f=62.959999999999994, latest_observation_utc=datetime.datetime(2026, 6, 20, 15, 5, tzinfo=datetime.timezone.utc), observation_count=95, model_future_high_f=69.31428571428572, model_details={'future_max_by_model': {'temperature_2m__gfs_seamless': 69.4, 'temperature_2m__gfs013': 69.2, 'temperature_2m__gfs_global': 69.2, 'temperature_2m__best_match': 69.4}, 'selected_future_high_f': 69.31428571428572, 'selected_model_components': [{'column': 'temperature_2m__gfs_seamless', 'model_id': 'gfs_seamless', 'future_high_f': 69.4, 'weight': 1.0}, {'column': 'temperature_2m__gfs013', 'model_id': 'gfs013', 'future_high_f': 69.2, 'weight': 0.75}, {'column': 'temperature_2m__gfs_global', 'model_id': 'gfs_global', 'future_high_f': 69.2, 'weight': 0.75}, {'column': 'temperature_2m__best_match', 'model_id': 'best_match', 'future_high_f': 69.4, 'weight': 1.0}], 'weights_used': {'gfs_seamless': 1.0, 'gfs013': 0.75, 'gfs_global': 0.75, 'best_match': 1.0}, 'successful_models': ['gfs_seamless', 'gfs013', 'gfs_global', 'best_match'], 'failed_models': {}, 'fallback_used': False, 'feature_summary': {'cloud_cover_low_max': 100.0, 'cloud_cover_low_mean': 16.8, 'shortwave_radiation_max': 1026.0, 'shortwave_radiation_mean': 502.8, 'direct_radiation_max': 929.0, 'direct_radiation_mean': 387.0, 'wind_speed_10m_max': 15.5, 'wind_speed_10m_mean': 10.139999999999999, 'wind_gusts_10m_max': 20.8, 'wind_gusts_10m_mean': 11.77777777777778, 'apparent_temperature_max': 72.7, 'apparent_temperature_mean': 65.35, 'wind_direction_10m_mean': 251.73333333333332}, 'failed_variable_requests': {}, 'raw_columns': ['time', 'temperature_2m__gfs_seamless', 'cloud_cover__gfs_seamless', 'cloud_cover_low__gfs_seamless', 'cloud_cover_mid__gfs_seamless', 'cloud_cover_high__gfs_seamless', 'shortwave_radiation__gfs_seamless', 'direct_radiation__gfs_seamless', 'diffuse_radiation__gfs_seamless', 'sunshine_duration__gfs_seamless', 'apparent_temperature__gfs_seamless', 'wind_speed_10m__gfs_seamless', 'wind_gusts_10m__gfs_seamless', 'wind_direction_10m__gfs_seamless', 'relative_humidity_2m__gfs_seamless', 'dew_point_2m__gfs_seamless', 'temperature_2m__gfs013', 'cloud_cover__gfs013', 'cloud_cover_low__gfs013', 'cloud_cover_mid__gfs013', 'cloud_cover_high__gfs013', 'shortwave_radiation__gfs013', 'direct_radiation__gfs013', 'diffuse_radiation__gfs013', 'sunshine_duration__gfs013', 'apparent_temperature__gfs013', 'wind_speed_10m__gfs013', 'wind_gusts_10m__gfs013', 'wind_direction_10m__gfs013', 'relative_humidity_2m__gfs013', 'dew_point_2m__gfs013', 'temperature_2m__gfs_global', 'cloud_cover__gfs_global', 'cloud_cover_low__gfs_global', 'cloud_cover_mid__gfs_global', 'cloud_cover_high__gfs_global', 'shortwave_radiation__gfs_global', 'direct_radiation__gfs_global', 'diffuse_radiation__gfs_global', 'sunshine_duration__gfs_global', 'apparent_temperature__gfs_global', 'wind_speed_10m__gfs_global', 'wind_gusts_10m__gfs_global', 'wind_direction_10m__gfs_global', 'relative_humidity_2m__gfs_global', 'dew_point_2m__gfs_global', 'temperature_2m__best_match', 'cloud_cover__best_match', 'cloud_cover_low__best_match', 'cloud_cover_mid__best_match', 'cloud_cover_high__best_match', 'shortwave_radiation__best_match', 'direct_radiation__best_match', 'diffuse_radiation__best_match', 'sunshine_duration__best_match', 'apparent_temperature__best_match', 'wind_speed_10m__best_match', 'wind_gusts_10m__best_match', 'wind_direction_10m__best_match', 'relative_humidity_2m__best_match', 'dew_point_2m__best_match']})",
      "open_meteo": {
        "successful_models": [
          "gfs_seamless",
          "gfs013",
          "gfs_global",
          "best_match"
        ],
        "failed_models": {},
        "fallback_used": false,
        "model_maxes_f": {
          "temperature_2m__gfs_seamless": 69.4,
          "temperature_2m__gfs013": 69.2,
          "temperature_2m__gfs_global": 69.2,
          "temperature_2m__best_match": 69.4
        },
        "feature_summary": {
          "cloud_cover_low_max": 100.0,
          "cloud_cover_low_mean": 16.8,
          "shortwave_radiation_max": 1026.0,
          "shortwave_radiation_mean": 502.8,
          "direct_radiation_max": 929.0,
          "direct_radiation_mean": 387.0,
          "wind_speed_10m_max": 15.5,
          "wind_speed_10m_mean": 10.139999999999999,
          "wind_gusts_10m_max": 20.8,
          "wind_gusts_10m_mean": 11.77777777777778,
          "apparent_temperature_max": 72.7,
          "apparent_temperature_mean": 65.35,
          "wind_direction_10m_mean": 251.73333333333332
        },
        "failed_variable_requests": {},
        "raw_columns": [
          "time",
          "temperature_2m__gfs_seamless",
          "cloud_cover__gfs_seamless",
          "cloud_cover_low__gfs_seamless",
          "cloud_cover_mid__gfs_seamless",
          "cloud_cover_high__gfs_seamless",
          "shortwave_radiation__gfs_seamless",
          "direct_radiation__gfs_seamless",
          "diffuse_radiation__gfs_seamless",
          "sunshine_duration__gfs_seamless",
          "apparent_temperature__gfs_seamless",
          "wind_speed_10m__gfs_seamless",
          "wind_gusts_10m__gfs_seamless",
          "wind_direction_10m__gfs_seamless",
          "relative_humidity_2m__gfs_seamless",
          "dew_point_2m__gfs_seamless",
          "temperature_2m__gfs013",
          "cloud_cover__gfs013",
          "cloud_cover_low__gfs013",
          "cloud_cover_mid__gfs013",
          "cloud_cover_high__gfs013",
          "shortwave_radiation__gfs013",
          "direct_radiation__gfs013",
          "diffuse_radiation__gfs013",
          "sunshine_duration__gfs013",
          "apparent_temperature__gfs013",
          "wind_speed_10m__gfs013",
          "wind_gusts_10m__gfs013",
          "wind_direction_10m__gfs013",
          "relative_humidity_2m__gfs013",
          "dew_point_2m__gfs013",
          "temperature_2m__gfs_global",
          "cloud_cover__gfs_global",
          "cloud_cover_low__gfs_global",
          "cloud_cover_mid__gfs_global",
          "cloud_cover_high__gfs_global",
          "shortwave_radiation__gfs_global",
          "direct_radiation__gfs_global",
          "diffuse_radiation__gfs_global",
          "sunshine_duration__gfs_global",
          "apparent_temperature__gfs_global",
          "wind_speed_10m__gfs_global",
          "wind_gusts_10m__gfs_global",
          "wind_direction_10m__gfs_global",
          "relative_humidity_2m__gfs_global",
          "dew_point_2m__gfs_global",
          "temperature_2m__best_match",
          "cloud_cover__best_match",
          "cloud_cover_low__best_match",
          "cloud_cover_mid__best_match",
          "cloud_cover_high__best_match",
          "shortwave_radiation__best_match",
          "direct_radiation__best_match",
          "diffuse_radiation__best_match",
          "sunshine_duration__best_match",
          "apparent_temperature__best_match",
          "wind_speed_10m__best_match",
          "wind_gusts_10m__best_match",
          "wind_direction_10m__best_match",
          "relative_humidity_2m__best_match",
          "dew_point_2m__best_match"
        ]
      }
    }
  },
  "threshold_sweep": {
    "status": "ok",
    "payload": {
      "available": true,
      "replay_supported": true
    }
  },
  "calibration_readiness": {
    "status": "ok",
    "payload": {
      "station": "KLAX",
      "readiness_level": "READY_FOR_SMOKE_CALIBRATION",
      "total_predictions": 252,
      "distinct_prediction_dates": 2,
      "prediction_dates": [
        "2026-06-19",
        "2026-06-20"
      ],
      "official_outcomes": 1,
      "official_outcome_dates": [
        "2026-06-19"
      ],
      "joined_rows": 174,
      "unique_joined_market_dates": 1,
      "joined_market_dates": [
        "2026-06-19"
      ],
      "latest_prediction_date": "2026-06-20",
      "latest_settlement_eligible_date": "2026-06-19",
      "settled_eligible_dates": [
        "2026-06-19"
      ],
      "missing_outcomes_by_date": [],
      "outcomes_exist_but_not_joined_dates": [],
      "unsettled_dates_skipped": [
        "2026-06-20"
      ],
      "minimum_smoke_rows": 1,
      "minimum_early_rows": 30,
      "minimum_early_market_dates": 5,
      "minimum_initial_validation_rows": 100,
      "minimum_initial_validation_market_dates": 15,
      "next_commands": [
        "kalshi-weather calibration-report --station KLAX",
        "kalshi-weather model-vs-market --station KLAX",
        "kalshi-weather model-health --station KLAX"
      ],
      "plain_english": "Enough to verify plumbing, not enough to trust edge."
    }
  },
  "calibration_report": {
    "status": "ok",
    "payload": {
      "joined_row_count": 174,
      "brier_score": 0.0,
      "log_loss": 1.0000005000290893e-06,
      "average_predicted_probability": 0.16666666666666666,
      "empirical_yes_rate": 0.16666666666666666,
      "warning": null,
      "calibration_buckets": [
        {
          "bucket_min": 0.0,
          "bucket_max": 0.1,
          "count": 145,
          "avg_probability": 0.0,
          "observed_rate": 0.0
        },
        {
          "bucket_min": 0.9,
          "bucket_max": 1.0,
          "count": 29,
          "avg_probability": 1.0,
          "observed_rate": 1.0
        }
      ],
      "by_bracket": {
        "Will the **high temp in LA** be >73\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_probability": 0.0,
          "yes_rate": 0.0
        },
        "Will the **high temp in LA** be <66\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_probability": 0.0,
          "yes_rate": 0.0
        },
        "Will the **high temp in LA** be 72-73\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_probability": 0.0,
          "yes_rate": 0.0
        },
        "Will the **high temp in LA** be 70-71\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_probability": 1.0,
          "yes_rate": 1.0
        },
        "Will the **high temp in LA** be 68-69\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_probability": 0.0,
          "yes_rate": 0.0
        },
        "Will the **high temp in LA** be 66-67\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_probability": 0.0,
          "yes_rate": 0.0
        }
      },
      "by_model_version": {
        "v0.2-openmeteo-per-model-normal-residual": {
          "count": 102,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        },
        "v0.3-openmeteo-weighted-normal-residual": {
          "count": 72,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        }
      },
      "by_market_date": {
        "2026-06-19": {
          "count": 174,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        }
      },
      "by_asof_hour": {
        "0": {
          "count": 42,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        },
        "1": {
          "count": 42,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        },
        "2": {
          "count": 18,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        },
        "3": {
          "count": 66,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        },
        "4": {
          "count": 6,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        }
      },
      "by_observed_inside_bracket": {
        "0": {
          "count": 174,
          "avg_probability": 0.16666666666666666,
          "yes_rate": 0.16666666666666666
        }
      }
    }
  },
  "residual_report": {
    "status": "ok",
    "payload": {
      "joined_row_count": 174,
      "residual_count": 174,
      "residual_mean": 6.83793103448276,
      "residual_median": 7.742857142857147,
      "residual_std": 1.1535499522968475,
      "residual_mae": 6.83793103448276,
      "residual_rmse": 6.933998179477038,
      "residual_percentiles": {
        "p10": 5.0,
        "p25": 6.600000000000001,
        "p50": 7.742857142857147,
        "p75": 7.742857142857147,
        "p90": 7.899999999999999
      },
      "suggested_residual_sigma_f": 1.1535499522968475,
      "by_asof_hour": {
        "0": {
          "count": 42,
          "avg_residual": 5.0,
          "sample_stddev": 0.0
        },
        "1": {
          "count": 42,
          "avg_residual": 6.600000000000001,
          "sample_stddev": 0.0
        },
        "2": {
          "count": 18,
          "avg_residual": 7.8999999999999995,
          "sample_stddev": 9.139280539960854e-16
        },
        "3": {
          "count": 66,
          "avg_residual": 7.742857142857147,
          "sample_stddev": 0.0
        },
        "4": {
          "count": 6,
          "avg_residual": 8.22857142857142,
          "sample_stddev": 0.0
        }
      },
      "by_bracket": {
        "Will the **high temp in LA** be >73\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_residual": 6.83793103448276,
          "sample_stddev": 1.1705900175736121
        },
        "Will the **high temp in LA** be <66\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_residual": 6.83793103448276,
          "sample_stddev": 1.1705900175736121
        },
        "Will the **high temp in LA** be 72-73\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_residual": 6.83793103448276,
          "sample_stddev": 1.1705900175736121
        },
        "Will the **high temp in LA** be 70-71\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_residual": 6.83793103448276,
          "sample_stddev": 1.1705900175736121
        },
        "Will the **high temp in LA** be 68-69\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_residual": 6.83793103448276,
          "sample_stddev": 1.1705900175736121
        },
        "Will the **high temp in LA** be 66-67\u00b0 on Jun 19, 2026?": {
          "count": 29,
          "avg_residual": 6.83793103448276,
          "sample_stddev": 1.1705900175736121
        }
      },
      "by_model_version": {
        "v0.2-openmeteo-per-model-normal-residual": {
          "count": 102,
          "avg_residual": 6.170588235294119,
          "sample_stddev": 1.0860582041633486
        },
        "v0.3-openmeteo-weighted-normal-residual": {
          "count": 72,
          "avg_residual": 7.783333333333336,
          "sample_stddev": 0.1351864138314982
        }
      },
      "warning": null
    }
  },
  "paper_report": {
    "status": "ok",
    "payload": {
      "total_paper_fills": 0,
      "realized_pnl": "0",
      "win_count": 0,
      "loss_count": 0,
      "trades_by_ticker": {},
      "top_tickers_by_fills": {},
      "entry_reasons": {},
      "exit_reasons": {},
      "open_positions": [],
      "estimated_unrealized_pnl": null,
      "average_entry_edge": null,
      "average_hold_time_minutes": null,
      "total_exposure": "0",
      "fills_by_day": {},
      "max_drawdown": null,
      "reset_events": [],
      "latest_cash": "1000.0",
      "current_cash": "1000.0",
      "latest_equity_record": {
        "id": 22,
        "created_utc": "2026-06-20T03:47:39.088754+00:00",
        "cash": "1000.0",
        "realized_pnl": "0",
        "payload_json": "{\"positions\": {}}"
      }
    }
  },
  "paper_replay": {
    "status": "ok",
    "payload": {
      "snapshots_scanned": 252,
      "simulated_entries": 4,
      "simulated_exits": 0,
      "open_replay_positions": [
        {
          "ticker": "KXHIGHLAX-26JUN20-B73.5",
          "side": "no",
          "entry_price": "0.7600",
          "edge": "0.2379",
          "asof_utc": "2026-06-20T14:40:41.669549+00:00"
        },
        {
          "ticker": "KXHIGHLAX-26JUN20-B71.5",
          "side": "no",
          "entry_price": "0.3600",
          "edge": "0.42785",
          "asof_utc": "2026-06-20T14:40:41.669549+00:00"
        },
        {
          "ticker": "KXHIGHLAX-26JUN20-B69.5",
          "side": "yes",
          "entry_price": "0.0800",
          "edge": "0.5946",
          "asof_utc": "2026-06-20T14:40:41.669549+00:00"
        },
        {
          "ticker": "KXHIGHLAX-26JUN20-B67.5",
          "side": "yes",
          "entry_price": "0.0100",
          "edge": "0.1005",
          "asof_utc": "2026-06-20T14:40:41.669549+00:00"
        }
      ],
      "realized_replay_pnl": "0",
      "max_drawdown": null,
      "average_hold_time": null,
      "trades_by_side": {
        "no": 2,
        "yes": 2
      },
      "trades_by_ticker": {
        "KXHIGHLAX-26JUN20-B73.5": 1,
        "KXHIGHLAX-26JUN20-B71.5": 1,
        "KXHIGHLAX-26JUN20-B69.5": 1,
        "KXHIGHLAX-26JUN20-B67.5": 1
      },
      "edge_threshold_used": "0.05",
      "total_hurdle": "0.05",
      "warnings": []
    }
  },
  "poc_demo": {
    "status": "ok",
    "payload": {
      "label": "DEMO DATA - NOT TRADING EVIDENCE",
      "station": "KLAX",
      "stored_to_production": false,
      "demo_inputs": {
        "observed_high_so_far_f": 70,
        "model_future_high_f": 72
      },
      "demo_opportunities": [
        {
          "ticker": "DEMO-T70",
          "best_side": "yes",
          "best_edge": "0.12",
          "would_trade": true,
          "reason": "fixture edge for plumbing demo"
        }
      ],
      "demo_threshold_sweep": [
        {
          "threshold": "0.05",
          "would_trade_count": 1
        }
      ],
      "demo_paper_simulation": {
        "entries": 1,
        "exits": 1,
        "realized_pnl": "0.04"
      },
      "demo_calibration": {
        "joined_row_count": 10,
        "brier_score": 0.06871999999999999,
        "log_loss": 0.2681508925742412,
        "average_predicted_probability": 0.5820000000000001,
        "empirical_yes_rate": 0.6,
        "warning": "fewer than 30 joined rows",
        "calibration_buckets": [
          {
            "bucket_min": 0.0,
            "bucket_max": 0.1,
            "count": 1,
            "avg_probability": 0.08,
            "observed_rate": 0.0
          },
          {
            "bucket_min": 0.1,
            "bucket_max": 0.2,
            "count": 1,
            "avg_probability": 0.18,
            "observed_rate": 0.0
          },
          {
            "bucket_min": 0.30000000000000004,
            "bucket_max": 0.4,
            "count": 1,
            "avg_probability": 0.31,
            "observed_rate": 0.0
          },
          {
            "bucket_min": 0.4,
            "bucket_max": 0.5,
            "count": 1,
            "avg_probability": 0.45,
            "observed_rate": 0.0
          },
          {
            "bucket_min": 0.5,
            "bucket_max": 0.6000000000000001,
            "count": 1,
            "avg_probability": 0.58,
            "observed_rate": 1.0
          },
          {
            "bucket_min": 0.6000000000000001,
            "bucket_max": 0.7000000000000001,
            "count": 1,
            "avg_probability": 0.68,
            "observed_rate": 1.0
          },
          {
            "bucket_min": 0.7000000000000001,
            "bucket_max": 0.8,
            "count": 1,
            "avg_probability": 0.79,
            "observed_rate": 1.0
          },
          {
            "bucket_min": 0.8,
            "bucket_max": 0.9,
            "count": 1,
            "avg_probability": 0.86,
            "observed_rate": 1.0
          },
          {
            "bucket_min": 0.9,
            "bucket_max": 1.0,
            "count": 2,
            "avg_probability": 0.9450000000000001,
            "observed_rate": 1.0
          }
        ],
        "by_bracket": {
          "demo-0": {
            "count": 1,
            "avg_probability": 0.08,
            "yes_rate": 0.0
          },
          "demo-1": {
            "count": 1,
            "avg_probability": 0.18,
            "yes_rate": 0.0
          },
          "demo-2": {
            "count": 1,
            "avg_probability": 0.31,
            "yes_rate": 0.0
          },
          "demo-3": {
            "count": 1,
            "avg_probability": 0.45,
            "yes_rate": 0.0
          },
          "demo-4": {
            "count": 1,
            "avg_probability": 0.58,
            "yes_rate": 1.0
          },
          "demo-5": {
            "count": 1,
            "avg_probability": 0.68,
            "yes_rate": 1.0
          },
          "demo-6": {
            "count": 1,
            "avg_probability": 0.79,
            "yes_rate": 1.0
          },
          "demo-7": {
            "count": 1,
            "avg_probability": 0.86,
            "yes_rate": 1.0
          },
          "demo-8": {
            "count": 1,
            "avg_probability": 0.92,
            "yes_rate": 1.0
          },
          "demo-9": {
            "count": 1,
            "avg_probability": 0.97,
            "yes_rate": 1.0
          }
        },
        "by_model_version": {
          "demo-fixture-model": {
            "count": 10,
            "avg_probability": 0.5820000000000001,
            "yes_rate": 0.6
          }
        },
        "by_market_date": {
          "2026-05-01": {
            "count": 1,
            "avg_probability": 0.08,
            "yes_rate": 0.0
          },
          "2026-05-02": {
            "count": 1,
            "avg_probability": 0.18,
            "yes_rate": 0.0
          },
          "2026-05-03": {
            "count": 1,
            "avg_probability": 0.31,
            "yes_rate": 0.0
          },
          "2026-05-04": {
            "count": 1,
            "avg_probability": 0.45,
            "yes_rate": 0.0
          },
          "2026-05-05": {
            "count": 1,
            "avg_probability": 0.58,
            "yes_rate": 1.0
          },
          "2026-05-06": {
            "count": 1,
            "avg_probability": 0.68,
            "yes_rate": 1.0
          },
          "2026-05-07": {
            "count": 1,
            "avg_probability": 0.79,
            "yes_rate": 1.0
          },
          "2026-05-08": {
            "count": 1,
            "avg_probability": 0.86,
            "yes_rate": 1.0
          },
          "2026-05-09": {
            "count": 1,
            "avg_probability": 0.92,
            "yes_rate": 1.0
          },
          "2026-05-10": {
            "count": 1,
            "avg_probability": 0.97,
            "yes_rate": 1.0
          }
        },
        "by_asof_hour": {
          "18": {
            "count": 2,
            "avg_probability": 0.435,
            "yes_rate": 0.5
          },
          "19": {
            "count": 2,
            "avg_probability": 0.52,
            "yes_rate": 0.5
          },
          "20": {
            "count": 2,
            "avg_probability": 0.615,
            "yes_rate": 0.5
          },
          "21": {
            "count": 2,
            "avg_probability": 0.71,
            "yes_rate": 0.5
          },
          "22": {
            "count": 1,
            "avg_probability": 0.58,
            "yes_rate": 1.0
          },
          "23": {
            "count": 1,
            "avg_probability": 0.68,
            "yes_rate": 1.0
          }
        },
        "by_observed_inside_bracket": {
          "0": {
            "count": 5,
            "avg_probability": 0.536,
            "yes_rate": 0.6
          },
          "1": {
            "count": 5,
            "avg_probability": 0.628,
            "yes_rate": 0.6
          }
        }
      }
    }
  }
}
- Production joined outcomes: 174.
- Calibration meaningful yet: no, unless production joined rows reach a useful sample size.
- Fake paper fills: 0.
- No-trade state is valid when configured edge thresholds are not cleared.
- Demo POC works offline but proves plumbing only, not edge.
- Before claiming edge: collect settled predictions, ingest official outcomes, join, and review calibration/replay.
- Before live-readiness discussion: add authentication, order guards, approvals, dry-run audits, and separate review.
