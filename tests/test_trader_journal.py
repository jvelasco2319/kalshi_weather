from kalshi_weather.trader_agent.journal import SqliteTraderJournal
from kalshi_weather.trader_agent.repo_adapter import trader_context_from_model_payload
from kalshi_weather.trader_agent.trader_types import MarketBracket
from kalshi_weather.cli import _trader_portfolio_snapshot


def _buy_order(price_cents: int = 40, quantity: int = 5) -> dict:
    return {
        "action": "PLACE_FAKE_LIMIT_BUY",
        "contract_ticker": "KXHIGHLAX-26JUN26-B70.5",
        "side": "YES",
        "limit_price_cents": price_cents,
        "quantity": quantity,
        "metadata": {
            "decision_id": "decision-1",
            "selected_candidate_id": "KXHIGHLAX-26JUN26-B70.5:YES:BUY",
            "bracket_label": "70-71",
            "fake_money_only": True,
        },
    }


def _taker_buy_order(price_cents: int = 42, quantity: int = 5, side: str = "YES") -> dict:
    return {
        "action": "EXECUTE_FAKE_TAKER_BUY",
        "contract_ticker": "KXHIGHLAX-26JUN26-B70.5",
        "side": side,
        "limit_price_cents": price_cents,
        "quantity": quantity,
        "metadata": {
            "decision_id": "decision-taker",
            "selected_candidate_id": f"KXHIGHLAX-26JUN26-B70.5:{side}:BUY",
            "bracket_label": "70-71",
            "fake_money_only": True,
            "selected_execution_style": "taker",
            "entry_price_source": "ask",
        },
    }


def test_trader_journal_executes_fake_buy_and_tracks_open_position(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    result = journal.execute_paper_order(_buy_order())

    assert result["executed"] is True
    positions = journal.load_open_positions()
    assert len(positions) == 1
    assert positions[0]["contract_ticker"] == "KXHIGHLAX-26JUN26-B70.5"
    assert positions[0]["quantity"] == 5
    assert positions[0]["avg_entry_price_cents"] == 40.0


def test_trader_journal_stages_limit_buy_until_ask_crosses(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    result = journal.execute_paper_order(
        _buy_order(price_cents=40, quantity=5),
        market_brackets=[
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=39,
                yes_ask_cents=42,
            )
        ],
    )

    assert result["executed"] is False
    assert result["status"] == "open"
    assert result["reason"] == "buy limit below current ask"
    assert journal.load_open_positions() == []
    assert len(journal.load_open_orders()) == 1

    fills = journal.process_pending_orders(
        [
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=38,
                yes_ask_cents=40,
            )
        ]
    )

    assert fills[0]["executed"] is True
    assert fills[0]["pending_order_filled"] is True
    assert journal.load_open_orders() == []
    positions = journal.load_open_positions()
    assert len(positions) == 1
    assert positions[0]["avg_entry_price_cents"] == 40.0


def test_passive_buy_fill_defaults_to_limit_price(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    result = journal.execute_paper_order(
        _buy_order(price_cents=36, quantity=5),
        market_brackets=[
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=30,
                yes_ask_cents=31,
            )
        ],
    )

    assert result["executed"] is True
    assert result["fill_price_cents"] == 36.0
    assert result["fill_limit_price_cents"] == 36.0
    assert result["fill_market_price_cents"] == 31
    assert result["fill_price_mode"] == "conservative"
    assert result["fill_price_improvement_cents"] == 0.0
    assert result["conservative_fill_adjustment_cents"] == 5.0
    assert journal.load_open_positions()[0]["avg_entry_price_cents"] == 36.0


def test_fill_price_improvement_only_when_enabled(tmp_path):
    conservative = SqliteTraderJournal(tmp_path / "conservative.sqlite")
    market = SqliteTraderJournal(tmp_path / "market.sqlite")
    bracket = MarketBracket(
        "KXHIGHLAX-26JUN26",
        "KXHIGHLAX-26JUN26-B70.5",
        "70-71",
        yes_bid_cents=30,
        yes_ask_cents=31,
    )

    conservative_result = conservative.execute_paper_order(_buy_order(price_cents=36), market_brackets=[bracket])
    market_result = market.execute_paper_order(
        _buy_order(price_cents=36),
        market_brackets=[bracket],
        fill_price_mode="market",
    )

    assert conservative_result["fill_price_cents"] == 36.0
    assert conservative_result["fill_price_source"] == "conservative_limit_price"
    assert market_result["fill_price_cents"] == 31
    assert market_result["fill_price_source"] == "market_price_improvement"
    assert market_result["fill_price_improvement_cents"] == 5.0


def test_taker_mode_immediate_fake_fill_and_no_open_order_created_after_buy(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    bracket = MarketBracket(
        "KXHIGHLAX-26JUN26",
        "KXHIGHLAX-26JUN26-B70.5",
        "70-71",
        yes_bid_cents=40,
        yes_ask_cents=42,
    )

    result = journal.execute_paper_order(_taker_buy_order(price_cents=42, quantity=5), market_brackets=[bracket])

    assert result["executed"] is True
    assert result["selected_execution_style"] == "taker"
    assert result["entry_price_source"] == "ask"
    assert result["fill_price_cents"] == 42.0
    assert result["fill_price_source"] == "taker_current_ask"
    assert journal.load_open_orders() == []
    positions = journal.load_open_positions()
    assert len(positions) == 1
    assert positions[0]["quantity"] == 5
    assert positions[0]["avg_entry_price_cents"] == 42.0


def test_taker_mode_fills_at_revalidated_current_ask_not_passive_limit(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    bracket = MarketBracket(
        "KXHIGHLAX-26JUN26",
        "KXHIGHLAX-26JUN26-B70.5",
        "70-71",
        yes_bid_cents=40,
        yes_ask_cents=41,
    )

    result = journal.execute_paper_order(_taker_buy_order(price_cents=42, quantity=5), market_brackets=[bracket])

    assert result["executed"] is True
    assert result["fill_limit_price_cents"] == 42.0
    assert result["fill_market_price_cents"] == 41
    assert result["fill_price_cents"] == 41.0
    assert journal.load_open_orders() == []
    assert journal.load_open_positions()[0]["avg_entry_price_cents"] == 41.0


def test_taker_mode_rejects_missing_ask(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    bracket = MarketBracket(
        "KXHIGHLAX-26JUN26",
        "KXHIGHLAX-26JUN26-B70.5",
        "70-71",
        yes_bid_cents=40,
        yes_ask_cents=None,
        no_bid_cents=None,
    )

    result = journal.execute_paper_order(_taker_buy_order(price_cents=42, quantity=5), market_brackets=[bracket])

    assert result["executed"] is False
    assert result["reason"] == "entry ask missing from current market snapshot"
    assert journal.load_open_positions() == []
    assert journal.load_open_orders() == []


def test_taker_close_uses_bid(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(_taker_buy_order(price_cents=42, quantity=5))
    bracket = MarketBracket(
        "KXHIGHLAX-26JUN26",
        "KXHIGHLAX-26JUN26-B70.5",
        "70-71",
        yes_bid_cents=55,
        yes_ask_cents=57,
    )

    result = journal.execute_paper_order(
        {
            "action": "CLOSE_FAKE_POSITION",
            "contract_ticker": "KXHIGHLAX-26JUN26-B70.5",
            "side": "YES",
            "limit_price_cents": 55,
            "quantity": 5,
            "metadata": {
                "decision_id": "decision-close",
                "selected_candidate_id": "KXHIGHLAX-26JUN26-B70.5:YES:CLOSE",
                "bracket_label": "70-71",
                "fake_money_only": True,
            },
        },
        market_brackets=[bracket],
    )

    assert result["executed"] is True
    assert result["fill_market_price_cents"] == 55
    assert result["fill_price_cents"] == 55.0
    assert result["fill_price_source"] == "conservative_limit_price"
    assert journal.load_open_positions() == []


def test_trader_journal_pending_buy_can_be_rejected_by_risk_callback(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(
        _buy_order(price_cents=40, quantity=5),
        market_brackets=[
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=39,
                yes_ask_cents=42,
            )
        ],
    )

    fills = journal.process_pending_orders(
        [
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=38,
                yes_ask_cents=40,
            )
        ],
        risk_check=lambda _order, _fill_price: "insufficient fake cash",
    )

    assert fills[0]["executed"] is False
    assert fills[0]["reason"] == "insufficient fake cash"
    assert fills[0]["pending_order_filled"] is False
    assert journal.load_open_positions() == []
    assert journal.load_open_orders() == []


def test_trader_journal_executes_fake_close_and_realizes_pnl(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(_buy_order(price_cents=40, quantity=5))
    result = journal.execute_paper_order(
        {
            "action": "CLOSE_FAKE_POSITION",
            "contract_ticker": "KXHIGHLAX-26JUN26-B70.5",
            "side": "YES",
            "limit_price_cents": 55,
            "quantity": 5,
            "metadata": {
                "decision_id": "decision-2",
                "selected_candidate_id": "KXHIGHLAX-26JUN26-B70.5:YES:CLOSE",
                "bracket_label": "70-71",
                "fake_money_only": True,
            },
        }
    )

    assert result["executed"] is True
    assert result["realized_pnl_dollars"] == 0.75
    assert journal.load_open_positions() == []


def test_trader_journal_stages_limit_close_until_bid_crosses(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(_buy_order(price_cents=40, quantity=5))
    close_order = {
        "action": "CLOSE_FAKE_POSITION",
        "contract_ticker": "KXHIGHLAX-26JUN26-B70.5",
        "side": "YES",
        "limit_price_cents": 55,
        "quantity": 5,
        "metadata": {
            "decision_id": "decision-2",
            "selected_candidate_id": "KXHIGHLAX-26JUN26-B70.5:YES:CLOSE",
            "bracket_label": "70-71",
            "fake_money_only": True,
        },
    }

    result = journal.execute_paper_order(
        close_order,
        market_brackets=[
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=53,
                yes_ask_cents=56,
            )
        ],
    )

    assert result["executed"] is False
    assert result["status"] == "open"
    assert result["reason"] == "sell limit above current bid"
    assert len(journal.load_open_positions()) == 1
    assert len(journal.load_open_orders()) == 1

    fills = journal.process_pending_orders(
        [
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=56,
                yes_ask_cents=58,
            )
        ]
    )

    assert fills[0]["executed"] is True
    assert fills[0]["realized_pnl_dollars"] == 0.75
    assert journal.load_open_positions() == []
    assert journal.load_open_orders() == []


def test_conservative_equity_removes_price_improvement(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    bracket = MarketBracket(
        "KXHIGHLAX-26JUN26",
        "KXHIGHLAX-26JUN26-B70.5",
        "70-71",
        yes_bid_cents=31,
        yes_ask_cents=31,
    )
    journal.execute_paper_order(
        _buy_order(price_cents=36, quantity=10),
        market_brackets=[bracket],
        fill_price_mode="market",
    )

    snapshot = _trader_portfolio_snapshot(
        {"market_brackets": [bracket.to_dict()]},
        journal.load_open_positions(),
        journal.load_fills(),
        starting_cash=1000.0,
    )

    assert snapshot["reported_equity"] == 1000.0
    assert snapshot["optimistic_fill_benefit_dollars"] == 0.5
    assert snapshot["conservative_equity"] == 999.5


def test_fill_revalidation_recomputes_fair_value(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(
        _buy_order(price_cents=40, quantity=5),
        market_brackets=[
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=39,
                yes_ask_cents=42,
            )
        ],
    )

    fills = journal.process_pending_orders(
        [
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=38,
                yes_ask_cents=39,
            )
        ],
        risk_check=lambda _order, fill_price: {
            "passed": True,
            "fill_revalidated_fair_value_cents": 62.4,
            "fill_revalidated_net_edge_cents": round(62.4 - fill_price, 4),
            "fill_revalidated_market_age_seconds": 0.0,
            "fill_revalidated_model_age_seconds": 0.0,
        },
    )

    assert fills[0]["executed"] is True
    assert fills[0]["fill_revalidated_fair_value_cents"] == 62.4
    assert fills[0]["fill_revalidated_net_edge_cents"] == 22.4
    assert fills[0]["fill_revalidated_market_age_seconds"] == 0.0
    assert fills[0]["fill_revalidated_model_age_seconds"] == 0.0


def test_fill_revalidation_rejects_when_fields_missing(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(
        _buy_order(price_cents=40, quantity=5),
        market_brackets=[
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=39,
                yes_ask_cents=42,
            )
        ],
    )

    fills = journal.process_pending_orders(
        [
            MarketBracket(
                "KXHIGHLAX-26JUN26",
                "KXHIGHLAX-26JUN26-B70.5",
                "70-71",
                yes_bid_cents=38,
                yes_ask_cents=39,
            )
        ],
        risk_check=lambda _order, _fill_price: {
            "passed": False,
            "reason": "fill_rejected_revalidation_incomplete",
            "failure_code": "fill_rejected_revalidation_incomplete",
        },
    )

    assert fills[0]["executed"] is False
    assert fills[0]["fill_revalidation_failure_code"] == "fill_rejected_revalidation_incomplete"
    assert journal.load_open_positions() == []
    assert journal.load_open_orders() == []


def test_no_real_order_code_added():
    import inspect
    import kalshi_weather.trader_agent.journal as journal_module

    source = inspect.getsource(journal_module.SqliteTraderJournal)

    assert "httpx" not in source
    assert "requests" not in source
    assert "create_order" not in source


def test_trader_context_includes_ledger_position_as_close_candidate(tmp_path):
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(_buy_order(price_cents=40, quantity=5))
    payload = {
        "generated_at_utc": "2026-06-25T18:00:00+00:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-06-26",
        "probabilities": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "market_ticker": "KXHIGHLAX-26JUN26-B70.5",
                "bracket_label": "70-71",
                "bracket_lower_f": 70,
                "bracket_upper_f": 71,
                "p_yes": 0.60,
                "yes_bid": "0.55",
                "yes_ask": "0.56",
                "no_bid": "0.44",
                "no_ask": "0.45",
            }
        ],
    }

    context = trader_context_from_model_payload(payload, positions=journal.load_open_positions())

    assert any(candidate.action == "CLOSE" for candidate in context.candidate_trades)
