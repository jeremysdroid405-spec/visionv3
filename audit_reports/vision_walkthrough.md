# Vision Walkthrough — Live SH vs Replay SH near-miss
_Generated_: 2026-05-09T16:23:36.778472+00:00

_Canary_: `sh_canary_c_1778342681`

## Live SH vision-state breakdown
```json
{
  "vision_score_None": 539,
  "vision_score_lt_80": 29,
  "vision_score_gte_80": 91,
  "v2_promoted_into_vs": 0,
  "vision_score_raw_positive": 120,
  "quality_source_insufficient": 539,
  "total": 659
}
```

## Live SH `vision_score_v2` distribution
```json
{
  "total": 659,
  "v2_buckets": [
    {
      "bucket": 0,
      "n": 12
    },
    {
      "bucket": 20,
      "n": 34
    },
    {
      "bucket": 30,
      "n": 267
    },
    {
      "bucket": 40,
      "n": 213
    },
    {
      "bucket": 50,
      "n": 65
    }
  ],
  "vs_buckets": [
    {
      "bucket": 20,
      "n": 2
    },
    {
      "bucket": 40,
      "n": 2
    },
    {
      "bucket": 60,
      "n": 6
    },
    {
      "bucket": 70,
      "n": 19
    },
    {
      "bucket": 80,
      "n": 54
    },
    {
      "bucket": 90,
      "n": 37
    }
  ]
}
```

## Replay SH-attempt `vision_score_v2` distribution
```json
{
  "total": 4962,
  "v2_buckets": [
    {
      "bucket": 0,
      "n": 1520
    },
    {
      "bucket": 20,
      "n": 1925
    },
    {
      "bucket": 30,
      "n": 1383
    },
    {
      "bucket": 40,
      "n": 134
    }
  ],
  "vs_buckets": []
}
```

## Live SH samples (full gate_details)
```json
{
  "samples": [
    {
      "vision_class": "v1_percentile_>=80",
      "fields": {
        "player": null,
        "stat_type": "AST",
        "line": 4.5,
        "recommendation": "OVER",
        "reference_odds": null,
        "vision_score": 83.9,
        "vision_score_raw": 0.0977,
        "vision_score_v2": 45.07,
        "quality_source": "dk",
        "tier": "safe_haven",
        "tier_reason": "gates_passed",
        "hit_rate_l20": 95.0,
        "cv": 0.3311,
        "edge_vs_fair": 0.1156,
        "p_true_active": 0.95,
        "tp_source": "one_sided",
        "is_alternate": null,
        "usage_vacuum_factor": null,
        "usage_spike": null,
        "matchup_strength": null,
        "pace_factor": null
      },
      "gates": {
        "direction_gate": {
          "passed": true,
          "actual": {
            "projection": 7.1763,
            "line": 4.5,
            "diff": 2.6763,
            "ratio_proj/line": 1.5947,
            "ratio_(line-proj)/line": -0.5947
          },
          "threshold": {
            "applies_to_sides": [
              "OVER"
            ],
            "min_projection_minus_line": 0.0
          },
          "note": "direction_check_OVER"
        },
        "edge_gate": {
          "passed": true,
          "actual": 11.559999999999999,
          "threshold": 0.0,
          "note": null
        },
        "hit_rate_gate": {
          "passed": true,
          "actual": 95.0,
          "threshold": {
            "min": 80.0,
            "window": "default",
            "l5_subgate_enforced": true
          },
          "note": null
        },
        "vision_score_gate": {
          "passed": true,
          "actual": 83.9,
          "threshold": {
            "min": 80.0
          },
          "note": null
        },
        "cv_gate": {
          "passed": true,
          "actual": 0.3311,
          "threshold": 0.45,
          "note": null
        },
        "market_structure_gate": {
          "passed": true,
          "actual": {
            "is_alt": false,
            "tp_source": "one_sided"
          },
          "threshold": {
            "reject_when": {
              "is_alt": true,
              "tp_source": "one_sided"
            }
          },
          "note": null
        }
      }
    },
    {
      "vision_class": "v1_percentile_>=80",
      "fields": {
        "player": null,
        "stat_type": "AST",
        "line": 4.5,
        "recommendation": "OVER",
        "reference_odds": null,
        "vision_score": 83.9,
        "vision_score_raw": 0.0977,
        "vision_score_v2": 45.07,
        "quality_source": "dk",
        "tier": "safe_haven",
        "tier_reason": "gates_passed",
        "hit_rate_l20": 95.0,
        "cv": 0.3311,
        "edge_vs_fair": 0.1156,
        "p_true_active": 0.95,
        "tp_source": "one_sided",
        "is_alternate": null,
        "usage_vacuum_factor": null,
        "usage_spike": null,
        "matchup_strength": null,
        "pace_factor": null
      },
      "gates": {
        "direction_gate": {
          "passed": true,
          "actual": {
            "projection": 7.1763,
            "line": 4.5,
            "diff": 2.6763,
            "ratio_proj/line": 1.5947,
            "ratio_(line-proj)/line": -0.5947
          },
          "threshold": {
            "applies_to_sides": [
              "OVER"
            ],
            "min_projection_minus_line": 0.0
          },
          "note": "direction_check_OVER"
        },
        "edge_gate": {
          "passed": true,
          "actual": 11.559999999999999,
          "threshold": 0.0,
          "note": null
        },
        "hit_rate_gate": {
          "passed": true,
          "actual": 95.0,
          "threshold": {
            "min": 80.0,
            "window": "default",
            "l5_subgate_enforced": true
          },
          "note": null
        },
        "vision_score_gate": {
          "passed": true,
          "actual": 83.9,
          "threshold": {
            "min": 80.0
          },
          "note": null
        },
        "cv_gate": {
          "passed": true,
          "actual": 0.3311,
          "threshold": 0.45,
          "note": null
        },
        "market_structure_gate": {
          "passed": true,
          "actual": {
            "is_alt": false,
            "tp_source": "one_sided"
          },
          "threshold": {
            "reject_when": {
              "is_alt": true,
              "tp_source": "one_sided"
            }
          },
          "note": null
        }
      }
    },
    {
      "vision_class": "vision_score_None_deferred",
      "fields": {
        "player": null,
        "stat_type": "player_rebounds_assists_alternate",
        "line": 10.5,
        "recommendation": "UNDER",
        "reference_odds": null,
        "vision_score": null,
        "vision_score_raw": null,
        "vision_score_v2": 32.97,
        "quality_source": "insufficient_market",
        "tier": "safe_haven",
        "tier_reason": "gates_passed",
        "hit_rate_l20": 75.0,
        "cv": 0.5216,
        "edge_vs_fair": null,
        "p_true_active": 0.8378,
        "tp_source": "devig",
        "is_alternate": null,
        "usage_vacuum_factor": null,
        "usage_spike": null,
        "matchup_strength": null,
        "pace_factor": null
      },
      "gates": {
        "direction_gate": {
          "passed": true,
          "actual": {
            "projection": 7.082,
            "line": 10.5,
            "diff": -3.418,
            "ratio_proj/line": 0.6745,
            "ratio_(line-proj)/line": 0.3255
          },
          "threshold": {
            "applies_to_sides": [
              "UNDER"
            ],
            "max_projection_minus_line": 0.0,
            "min_line_minus_projection_ratio": 0.15
          },
          "note": "direction_check_UNDER"
        },
        "hit_rate_gate": {
          "passed": true,
          "actual": 75.0,
          "threshold": {
            "min": 65.0,
            "window": "default",
            "l5_subgate_enforced": true
          },
          "note": null
        },
        "cv_gate": {
          "passed": true,
          "actual": 0.5216,
          "threshold": 0.55,
          "note": "cv_cap_relaxed_hr>=75.0_+0.1"
        },
        "vision_score_gate": {
          "passed": true,
          "actual": null,
          "threshold": {
            "min": 80.0
          },
          "note": "vision_score_deferred_to_slate_pass"
        },
        "market_structure_gate": {
          "passed": true,
          "actual": {
            "is_alt": true,
            "tp_source": "devig"
          },
          "threshold": {
            "reject_when": {
              "is_alt": true,
              "tp_source": "one_sided"
            }
          },
          "note": null
        }
      }
    },
    {
      "vision_class": "vision_score_None_deferred",
      "fields": {
        "player": null,
        "stat_type": "player_rebounds_assists_alternate",
        "line": 9.5,
        "recommendation": "UNDER",
        "reference_odds": null,
        "vision_score": null,
        "vision_score_raw": null,
        "vision_score_v2": 31.65,
        "quality_source": "insufficient_market",
        "tier": "safe_haven",
        "tier_reason": "gates_passed",
        "hit_rate_l20": 75.0,
        "cv": 0.5216,
        "edge_vs_fair": null,
        "p_true_active": 0.7571,
        "tp_source": "devig",
        "is_alternate": null,
        "usage_vacuum_factor": null,
        "usage_spike": null,
        "matchup_strength": null,
        "pace_factor": null
      },
      "gates": {
        "direction_gate": {
          "passed": true,
          "actual": {
            "projection": 7.082,
            "line": 9.5,
            "diff": -2.418,
            "ratio_proj/line": 0.7455,
            "ratio_(line-proj)/line": 0.2545
          },
          "threshold": {
            "applies_to_sides": [
              "UNDER"
            ],
            "max_projection_minus_line": 0.0,
            "min_line_minus_projection_ratio": 0.15
          },
          "note": "direction_check_UNDER"
        },
        "hit_rate_gate": {
          "passed": true,
          "actual": 75.0,
          "threshold": {
            "min": 65.0,
            "window": "default",
            "l5_subgate_enforced": true
          },
          "note": null
        },
        "cv_gate": {
          "passed": true,
          "actual": 0.5216,
          "threshold": 0.55,
          "note": "cv_cap_relaxed_hr>=75.0_+0.1"
        },
        "vision_score_gate": {
          "passed": true,
          "actual": null,
          "threshold": {
            "min": 80.0
          },
          "note": "vision_score_deferred_to_slate_pass"
        },
        "market_structure_gate": {
          "passed": true,
          "actual": {
            "is_alt": true,
            "tp_source": "devig"
          },
          "threshold": {
            "reject_when": {
              "is_alt": true,
              "tp_source": "one_sided"
            }
          },
          "note": null
        }
      }
    }
  ]
}
```

## Replay SH-routed top-5 near-misses (full re-eval)

### `jarrett allen` REB 7.5 OVER @ -475
```json
{
  "tier": "unqualified",
  "tier_reason": "safe_haven_failed: gate_cv_fail",
  "vision_score_v2": 46.0,
  "vision_score": null,
  "vision_score_raw": 0.067539,
  "v2_components": {
    "edge_component": 0.4238368,
    "consistency_component": 0.8084,
    "context_component": 0.6080797413793103,
    "market_confidence_component": 0.5
  },
  "prop_inputs": {
    "side": "OVER",
    "line": 7.5,
    "ref_odds": -475,
    "vk2_projection": 11.6218,
    "vk2_sigma": 2.4577,
    "p_model": 0.953238,
    "p_true": 83.6,
    "edge_pct": 12.715104,
    "cv": 0.2832,
    "hit_rate_l20": 95.0,
    "ceiling_rate": 0.95,
    "books_available": 3,
    "tp_source": "one_sided",
    "tp_books_used": null,
    "usage_vacuum_factor": 1.06,
    "usage_spike": false,
    "matchup_strength": 0.8620689655172413,
    "pace_factor": 1.0041
  },
  "tier_gate_results": {
    "direction_gate": {
      "threshold": {
        "applies_to_sides": [
          "OVER"
        ],
        "min_projection_minus_line": 0.0
      },
      "value": {
        "projection": 11.6218,
        "line": 7.5,
        "diff": 4.1218,
        "ratio_proj/line": 1.5496,
        "ratio_(line-proj)/line": -0.5496
      },
      "passed": true,
      "note": "direction_check_OVER",
      "reason_code": null
    },
    "edge_gate": {
      "threshold": 0.0,
      "value": 12.715104,
      "passed": true,
      "reason_code": null
    },
    "hit_rate_gate": {
      "threshold": {
        "min": 80.0,
        "window": "default",
        "l5_subgate_enforced": true
      },
      "value": 95.0,
      "passed": true,
      "reason_code": null
    },
    "vision_score_gate": {
      "threshold": {
        "min": 80.0
      },
      "value": null,
      "passed": true,
      "note": "vision_score_deferred_to_slate_pass",
      "reason_code": null
    },
    "cv_gate": {
      "threshold": {
        "caps": {
          "pts": 0.4,
          "pra": 0.4,
          "reb": 0.45,
          "ast": 0.45,
          "threes": 0.55,
          "pts_reb": 0.45,
          "pts_ast": 0.45,
          "reb_ast": 0.45,
          "stl": 0.55,
          "blk": 0.55,
          "turnovers": 0.55
        },
        "family": "_default"
      },
      "value": 0.2832,
      "passed": false,
      "note": "cv_gate_no_cap_for_stat_family",
      "reason_code": "gate_cv_fail"
    },
    "market_structure_gate": {
      "threshold": {
        "reject_when": {
          "is_alt": true,
          "tp_source": "one_sided"
        }
      },
      "value": {
        "is_alt": false,
        "tp_source": null
      },
      "passed": true,
      "reason_code": null
    }
  }
}
```

### `jarrett allen` REB 7.5 OVER @ -475
```json
{
  "tier": "unqualified",
  "tier_reason": "safe_haven_failed: gate_cv_fail",
  "vision_score_v2": 46.0,
  "vision_score": null,
  "vision_score_raw": 0.067539,
  "v2_components": {
    "edge_component": 0.4238368,
    "consistency_component": 0.8084,
    "context_component": 0.6080797413793103,
    "market_confidence_component": 0.5
  },
  "prop_inputs": {
    "side": "OVER",
    "line": 7.5,
    "ref_odds": -475,
    "vk2_projection": 11.6218,
    "vk2_sigma": 2.4577,
    "p_model": 0.953238,
    "p_true": 83.6,
    "edge_pct": 12.715104,
    "cv": 0.2832,
    "hit_rate_l20": 95.0,
    "ceiling_rate": 0.95,
    "books_available": 3,
    "tp_source": "one_sided",
    "tp_books_used": null,
    "usage_vacuum_factor": 1.06,
    "usage_spike": false,
    "matchup_strength": 0.8620689655172413,
    "pace_factor": 1.0041
  },
  "tier_gate_results": {
    "direction_gate": {
      "threshold": {
        "applies_to_sides": [
          "OVER"
        ],
        "min_projection_minus_line": 0.0
      },
      "value": {
        "projection": 11.6218,
        "line": 7.5,
        "diff": 4.1218,
        "ratio_proj/line": 1.5496,
        "ratio_(line-proj)/line": -0.5496
      },
      "passed": true,
      "note": "direction_check_OVER",
      "reason_code": null
    },
    "edge_gate": {
      "threshold": 0.0,
      "value": 12.715104,
      "passed": true,
      "reason_code": null
    },
    "hit_rate_gate": {
      "threshold": {
        "min": 80.0,
        "window": "default",
        "l5_subgate_enforced": true
      },
      "value": 95.0,
      "passed": true,
      "reason_code": null
    },
    "vision_score_gate": {
      "threshold": {
        "min": 80.0
      },
      "value": null,
      "passed": true,
      "note": "vision_score_deferred_to_slate_pass",
      "reason_code": null
    },
    "cv_gate": {
      "threshold": {
        "caps": {
          "pts": 0.4,
          "pra": 0.4,
          "reb": 0.45,
          "ast": 0.45,
          "threes": 0.55,
          "pts_reb": 0.45,
          "pts_ast": 0.45,
          "reb_ast": 0.45,
          "stl": 0.55,
          "blk": 0.55,
          "turnovers": 0.55
        },
        "family": "_default"
      },
      "value": 0.2832,
      "passed": false,
      "note": "cv_gate_no_cap_for_stat_family",
      "reason_code": "gate_cv_fail"
    },
    "market_structure_gate": {
      "threshold": {
        "reject_when": {
          "is_alt": true,
          "tp_source": "one_sided"
        }
      },
      "value": {
        "is_alt": false,
        "tp_source": null
      },
      "passed": true,
      "reason_code": null
    }
  }
}
```

### `jarrett allen` REB 7.5 OVER @ -475
```json
{
  "tier": "unqualified",
  "tier_reason": "safe_haven_failed: gate_cv_fail",
  "vision_score_v2": 46.0,
  "vision_score": null,
  "vision_score_raw": 0.067539,
  "v2_components": {
    "edge_component": 0.4238368,
    "consistency_component": 0.8084,
    "context_component": 0.6080797413793103,
    "market_confidence_component": 0.5
  },
  "prop_inputs": {
    "side": "OVER",
    "line": 7.5,
    "ref_odds": -475,
    "vk2_projection": 11.6218,
    "vk2_sigma": 2.4577,
    "p_model": 0.953238,
    "p_true": 83.6,
    "edge_pct": 12.715104,
    "cv": 0.2832,
    "hit_rate_l20": 95.0,
    "ceiling_rate": 0.95,
    "books_available": 3,
    "tp_source": "one_sided",
    "tp_books_used": null,
    "usage_vacuum_factor": 1.06,
    "usage_spike": false,
    "matchup_strength": 0.8620689655172413,
    "pace_factor": 1.0041
  },
  "tier_gate_results": {
    "direction_gate": {
      "threshold": {
        "applies_to_sides": [
          "OVER"
        ],
        "min_projection_minus_line": 0.0
      },
      "value": {
        "projection": 11.6218,
        "line": 7.5,
        "diff": 4.1218,
        "ratio_proj/line": 1.5496,
        "ratio_(line-proj)/line": -0.5496
      },
      "passed": true,
      "note": "direction_check_OVER",
      "reason_code": null
    },
    "edge_gate": {
      "threshold": 0.0,
      "value": 12.715104,
      "passed": true,
      "reason_code": null
    },
    "hit_rate_gate": {
      "threshold": {
        "min": 80.0,
        "window": "default",
        "l5_subgate_enforced": true
      },
      "value": 95.0,
      "passed": true,
      "reason_code": null
    },
    "vision_score_gate": {
      "threshold": {
        "min": 80.0
      },
      "value": null,
      "passed": true,
      "note": "vision_score_deferred_to_slate_pass",
      "reason_code": null
    },
    "cv_gate": {
      "threshold": {
        "caps": {
          "pts": 0.4,
          "pra": 0.4,
          "reb": 0.45,
          "ast": 0.45,
          "threes": 0.55,
          "pts_reb": 0.45,
          "pts_ast": 0.45,
          "reb_ast": 0.45,
          "stl": 0.55,
          "blk": 0.55,
          "turnovers": 0.55
        },
        "family": "_default"
      },
      "value": 0.2832,
      "passed": false,
      "note": "cv_gate_no_cap_for_stat_family",
      "reason_code": "gate_cv_fail"
    },
    "market_structure_gate": {
      "threshold": {
        "reject_when": {
          "is_alt": true,
          "tp_source": "one_sided"
        }
      },
      "value": {
        "is_alt": false,
        "tp_source": null
      },
      "passed": true,
      "reason_code": null
    }
  }
}
```

### `donovan mitchell` PRA 42.5 UNDER @ -310
```json
{
  "tier": "unqualified",
  "tier_reason": "safe_haven_failed: gate_hit_rate_fail",
  "vision_score_v2": 45.94,
  "vision_score": null,
  "vision_score_raw": null,
  "v2_components": {
    "edge_component": 0.7651881333333334,
    "consistency_component": 0.29325,
    "context_component": 0.4525862068965517,
    "market_confidence_component": 0.28
  },
  "prop_inputs": {
    "side": "UNDER",
    "line": 42.5,
    "ref_odds": -310,
    "vk2_projection": 23.8655,
    "vk2_sigma": 8.5179,
    "p_model": 0.985654,
    "p_true": 70.8,
    "edge_pct": 22.955644,
    "cv": 0.4135,
    "hit_rate_l20": 50.0,
    "ceiling_rate": 0.5,
    "books_available": 1,
    "tp_source": "devig",
    "tp_books_used": null,
    "usage_vacuum_factor": 1.0,
    "usage_spike": false,
    "matchup_strength": 0.6896551724137931,
    "pace_factor": 0.9927
  },
  "tier_gate_results": {
    "direction_gate": {
      "threshold": {
        "applies_to_sides": [
          "UNDER"
        ],
        "max_projection_minus_line": 0.0,
        "min_line_minus_projection_ratio": 0.15
      },
      "value": {
        "projection": 23.8655,
        "line": 42.5,
        "diff": -18.6345,
        "ratio_proj/line": 0.5615,
        "ratio_(line-proj)/line": 0.4385
      },
      "passed": true,
      "note": "direction_check_UNDER",
      "reason_code": null
    },
    "hit_rate_gate": {
      "threshold": {
        "min": 65.0,
        "window": "default",
        "l5_subgate_enforced": true
      },
      "value": 50.0,
      "passed": false,
      "reason_code": "gate_hit_rate_fail"
    },
    "cv_gate": {
      "threshold": {
        "caps": {
          "pts": 0.4,
          "pra": 0.4,
          "reb": 0.45,
          "ast": 0.45,
          "threes": 0.55,
          "pts_reb": 0.45,
          "pts_ast": 0.45,
          "reb_ast": 0.45,
          "stl": 0.55,
          "blk": 0.55,
          "turnovers": 0.55
        },
        "family": "_default"
      },
      "value": 0.4135,
      "passed": false,
      "note": "cv_gate_no_cap_for_stat_family",
      "reason_code": "gate_cv_fail"
    },
    "vision_score_gate": {
      "threshold": {
        "min": 80.0
      },
      "value": null,
      "passed": true,
      "note": "vision_score_deferred_to_slate_pass",
      "reason_code": null
    },
    "market_structure_gate": {
      "threshold": {
        "reject_when": {
          "is_alt": true,
          "tp_source": "one_sided"
        }
      },
      "value": {
        "is_alt": false,
        "tp_source": null
      },
      "passed": true,
      "reason_code": null
    }
  }
}
```

### `luka doncic` PRA 57.5 UNDER @ -320
```json
{
  "tier": "unqualified",
  "tier_reason": "safe_haven_failed: gate_hit_rate_fail",
  "vision_score_v2": 45.71,
  "vision_score": null,
  "vision_score_raw": null,
  "v2_components": {
    "edge_component": 0.7608274666666667,
    "consistency_component": 0.21905000000000002,
    "context_component": 0.46143750000000006,
    "market_confidence_component": 0.4
  },
  "prop_inputs": {
    "side": "UNDER",
    "line": 57.5,
    "ref_odds": -320,
    "vk2_projection": 37.6352,
    "vk2_sigma": 8.5179,
    "p_model": 0.990153,
    "p_true": 71.2,
    "edge_pct": 22.824824,
    "cv": 0.5619,
    "hit_rate_l20": 35.0,
    "ceiling_rate": 0.5,
    "books_available": 2,
    "tp_source": "devig",
    "tp_books_used": null,
    "usage_vacuum_factor": 1.127,
    "usage_spike": false,
    "matchup_strength": 0.4482758620689655,
    "pace_factor": 1.0109
  },
  "tier_gate_results": {
    "direction_gate": {
      "threshold": {
        "applies_to_sides": [
          "UNDER"
        ],
        "max_projection_minus_line": 0.0,
        "min_line_minus_projection_ratio": 0.15
      },
      "value": {
        "projection": 37.6352,
        "line": 57.5,
        "diff": -19.8648,
        "ratio_proj/line": 0.6545,
        "ratio_(line-proj)/line": 0.3455
      },
      "passed": true,
      "note": "direction_check_UNDER",
      "reason_code": null
    },
    "hit_rate_gate": {
      "threshold": {
        "min": 65.0,
        "window": "default",
        "l5_subgate_enforced": true
      },
      "value": 35.0,
      "passed": false,
      "reason_code": "gate_hit_rate_fail"
    },
    "cv_gate": {
      "threshold": {
        "caps": {
          "pts": 0.4,
          "pra": 0.4,
          "reb": 0.45,
          "ast": 0.45,
          "threes": 0.55,
          "pts_reb": 0.45,
          "pts_ast": 0.45,
          "reb_ast": 0.45,
          "stl": 0.55,
          "blk": 0.55,
          "turnovers": 0.55
        },
        "family": "_default"
      },
      "value": 0.5619,
      "passed": false,
      "note": "cv_gate_no_cap_for_stat_family",
      "reason_code": "gate_cv_fail"
    },
    "vision_score_gate": {
      "threshold": {
        "min": 80.0
      },
      "value": null,
      "passed": true,
      "note": "vision_score_deferred_to_slate_pass",
      "reason_code": null
    },
    "market_structure_gate": {
      "threshold": {
        "reject_when": {
          "is_alt": true,
          "tp_source": "one_sided"
        }
      },
      "value": {
        "is_alt": false,
        "tp_source": null
      },
      "passed": true,
      "reason_code": null
    }
  }
}
```
