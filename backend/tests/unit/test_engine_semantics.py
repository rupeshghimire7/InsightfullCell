"""Meanings, units and personal-data flags (ingest), and profiles (profile)."""

from __future__ import annotations

import json

from insightfulcell.engine.semantics import ParseKind, name_tokens
from insightfulcell.engine.types import Meaning, PersonalData
from tests.unit.engine_support import analyse, by_name, table


def test_name_tokens_split_snake_camel_and_symbols() -> None:
    assert name_tokens("Labour Cost (AUD)") == ("labour", "cost", "aud")
    assert name_tokens("signupDate") == ("signup", "date")
    assert name_tokens("GST %") == ("gst", "%")


class TestPersonalData:
    def test_values_alone_flag_emails_and_phones(self) -> None:
        rows = [(f"user{i}@example.com", f"+61 4{i:02d} 555 {i:03d}") for i in range(20)]
        with table([("contact_a", "VARCHAR"), ("contact_b", "VARCHAR")], rows) as con:
            found = by_name(analyse(con, [("contact_a", "VARCHAR"), ("contact_b", "VARCHAR")])[0])
        assert found["contact_a"].personal_data is PersonalData.EMAIL
        assert found["contact_b"].personal_data is PersonalData.PHONE

    def test_names_that_say_personal_data_are_enough(self) -> None:
        cols = [("Customer Name", "VARCHAR"), ("dob", "DATE"), ("Street Address", "VARCHAR")]
        with table(cols, [("x", "2000-01-01", "y")] * 3) as con:
            found = by_name(analyse(con, cols)[0])
        assert found["Customer Name"].personal_data is PersonalData.PERSON_NAME
        assert found["dob"].personal_data is PersonalData.DATE_OF_BIRTH
        assert found["Street Address"].personal_data is PersonalData.ADDRESS

    def test_a_bare_name_column_is_a_person_only_if_the_values_say_so(self) -> None:
        cols = [("name", "VARCHAR")]
        with table(cols, [(f"SKU-{i}",) for i in range(12)]) as con:
            assert analyse(con, cols)[0][0].personal_data is None
        people = ["Olivia Harris", "Noah Brown", "Mia Patel", "Raj Singh"] * 3
        with table(cols, [(p,) for p in people]) as con:
            assert analyse(con, cols)[0][0].personal_data is PersonalData.PERSON_NAME
        with table([("store_name", "VARCHAR")], [(p,) for p in people]) as con:
            assert analyse(con, [("store_name", "VARCHAR")])[0][0].personal_data is None

    def test_reasons_never_quote_a_value(self) -> None:
        cols = [("who", "VARCHAR")]
        rows = [(f"secret{i}@corp.example",) for i in range(10)]
        with table(cols, rows) as con:
            found, profile = analyse(con, cols)
        assert "secret" not in json.dumps([s.model_dump(mode="json") for s in found])
        # Masked from the profile on: counts only.
        column = profile.columns[0]
        assert column.personal_data == "email"
        assert column.categorical is None
        assert "secret" not in column.model_dump_json()


class TestMeanings:
    def test_text_that_is_really_money_percent_dates_or_yes_no(self) -> None:
        cols = [
            ("fee", "VARCHAR"),
            ("discount", "VARCHAR"),
            ("joined", "VARCHAR"),
            ("paid", "VARCHAR"),
        ]
        rows = [
            ("$12.99", "10%", "15/11/2025", "Yes"),
            ("$1,234.50", "5%", "16/11/2025", "no"),
            ("(45.00)", "0%", "17/11/2025", "TRUE"),
        ] * 4
        with table(cols, rows) as con:
            found = by_name(analyse(con, cols)[0])
        assert (found["fee"].meaning, found["fee"].unit) == (Meaning.CURRENCY, "$")
        assert found["fee"].parse is not None
        assert found["fee"].parse.kind is ParseKind.NUMBERS
        assert (found["discount"].meaning, found["discount"].unit) == (Meaning.PERCENTAGE, "%")
        joined = found["joined"]
        assert joined.meaning is Meaning.DATETIME
        assert joined.parse is not None
        assert joined.parse.day_first is True  # 15/11 cannot be month-first
        assert found["paid"].parse is not None
        assert found["paid"].parse.kind is ParseKind.BOOLEANS

    def test_ambiguous_day_month_is_counted(self) -> None:
        cols = [("created", "VARCHAR")]
        with table(cols, [("03/04/2025",), ("05/06/2025",), ("2025-01-02",)]) as con:
            hint = analyse(con, cols)[0][0].parse
        assert hint is not None
        assert hint.ambiguous_day_month == 2

    def test_a_version_number_is_not_a_date(self) -> None:
        cols = [("app_version", "VARCHAR")]
        with table(cols, [("2.9.8",), ("3.1.4",), ("3.0.0",)]) as con:
            assert analyse(con, cols)[0][0].meaning is not Meaning.DATETIME

    def test_identifiers_geography_and_units_from_names(self) -> None:
        cols = [
            ("job_id", "BIGINT"),
            ("postcode", "VARCHAR"),
            ("distance_km", "DOUBLE"),
            ("churn_rate", "DOUBLE"),
        ]
        rows = [(i, f"{2600 + i}", i * 1.5, i / 100) for i in range(20)]
        with table(cols, rows) as con:
            found = by_name(analyse(con, cols)[0])
        assert found["job_id"].meaning is Meaning.IDENTIFIER
        assert found["postcode"].meaning is Meaning.GEOGRAPHY
        assert (found["distance_km"].meaning, found["distance_km"].unit) == (
            Meaning.CONTINUOUS,
            "km",
        )
        assert found["churn_rate"].meaning is Meaning.PERCENTAGE


class TestProfile:
    def test_sentinels_outliers_and_missing_tokens(self) -> None:
        cols = [("hours", "DOUBLE"), ("note", "VARCHAR")]
        rows = [(float(i % 7 + 1), "ok") for i in range(40)] + [(-999.0, "N/A"), (-999.0, "-")]
        with table(cols, rows) as con:
            _, profile = analyse(con, cols)
        hours, note = profile.columns
        assert hours.numeric is not None
        assert hours.numeric.sentinels == (-999.0,)
        assert hours.numeric.sentinel_rows == 2
        assert "sentinel_values" in hours.flags
        assert note.missing_tokens == 2
        assert "missing_tokens" in note.flags

    def test_case_variants_whitespace_duplicates_and_patterns(self) -> None:
        cols = [("state", "VARCHAR"), ("score", "BIGINT")]
        rows = [
            ("NSW", 1),
            ("nsw", 2),
            (" NSW", 3),
            ("VIC", None),
            ("VIC", None),
            ("QLD", 4),
            ("QLD", 4),
        ]
        with table(cols, rows) as con:
            _, profile = analyse(con, cols)
        state = profile.columns[0]
        assert state.categorical is not None
        assert {"case_variants", "whitespace"} <= set(state.flags)
        assert profile.table.duplicate_rows == 2  # (VIC, None) and (QLD, 4)
        assert profile.table.missing_patterns[0].columns == ("score",)
        assert profile.table.missing_patterns[0].rows == 2

    def test_dates_in_the_future_are_judged_against_the_run_date(self) -> None:
        cols = [("created", "DATE")]
        with table(cols, [("2026-01-01",), ("2027-06-01",)]) as con:
            _, profile = analyse(con, cols)
        dates = profile.columns[0].datetime
        assert dates is not None
        assert dates.future_count == 1
        assert "future_dates" in profile.columns[0].flags

    def test_text_that_parses_is_profiled_as_what_it_parses_to(self) -> None:
        cols = [("fee", "VARCHAR")]
        with table(cols, [("$10",), ("$20",), ("$30",), ("$40",), ("abc",)] * 4) as con:
            _, profile = analyse(con, cols)
        fee = profile.columns[0]
        assert fee.numeric is not None
        assert fee.numeric.p50 == 25.0
        assert fee.unparsed == 4
        assert "unparsed_values" in fee.flags
