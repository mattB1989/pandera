"""Polars dtype tests."""
import decimal
from decimal import Decimal
from typing import Union, Tuple, Sequence

from hypothesis import strategies as st, settings
import pytest
from hypothesis import given
from polars.testing import assert_frame_equal
from polars.testing.parametric import dataframes
import polars as pl

import pandera.errors
from pandera.api.polars.types import PolarsData
from pandera.engines import polars_engine as pe
from pandera.engines.polars_engine import (
    polars_object_coercible,
    COERCIBLE_KEY,
)


def convert_object_to_decimal(
    number: Union[Decimal, float, str, Tuple[int, Sequence[int], int]],
    precision: int,
    scale: int,
) -> decimal.Decimal:
    """Convert number to decimal with precision and scale."""
    decimal.getcontext().prec = precision
    return decimal.Decimal(number).quantize(
        decimal.Decimal(f"1e-{scale}"), decimal.ROUND_HALF_UP
    )


numeric_dtypes = [
    pe.Int8,
    pe.Int16,
    pe.Int32,
    pe.Int64,
    pe.UInt8,
    pe.UInt16,
    pe.UInt32,
    pe.UInt64,
    pe.Float32,
    pe.Float64,
]

temporal_types = [pe.Date, pe.DateTime, pe.Time, pe.Timedelta]

other_types = [
    pe.Categorical,
    pe.Bool,
    pe.String,
]

special_types = [
    pe.Decimal,
    pe.Object,
    pe.Null,
    pe.Category,
]

all_types = numeric_dtypes + temporal_types + other_types


def get_dataframe_strategy(type_: pl.DataType) -> st.SearchStrategy:
    """Get a strategy for a polars dataframe of a given dtype."""
    return dataframes(
        cols=2, lazy=True, allowed_dtypes=type_, null_probability=0.1, size=10
    )


# Hypothesis slow if test is failing
@pytest.mark.parametrize("dtype", all_types)
@given(st.data())
@settings(max_examples=1)
def test_coerce_no_cast(dtype, data):
    """Test that dtypes can be coerced without casting."""
    pandera_dtype = dtype()
    df = data.draw(get_dataframe_strategy(type_=pandera_dtype.type))
    coerced = pandera_dtype.coerce(data_container=PolarsData(df))
    assert_frame_equal(df, coerced)


@pytest.mark.parametrize(
    "to_dtype, strategy",
    [
        (pe.Null(), pl.LazyFrame([[None, None, None]])),
        (pe.Object(), pl.LazyFrame([[1, 2, 3]]).cast(pl.Object)),
        (
            pe.Category(categories=["a", "b", "c"]),
            pl.LazyFrame([["a", "b", "c"]]).cast(pl.Utf8),
        ),
    ],
)
def test_coerce_no_cast_special(to_dtype, strategy):
    """Test that dtypes can be coerced without casting."""
    coerced = to_dtype.coerce(data_container=strategy)
    for dtype in coerced.dtypes:
        assert dtype == to_dtype.type


@pytest.mark.parametrize(
    "from_dtype, to_dtype, strategy",
    [
        (pe.UInt32(), pe.UInt64(), get_dataframe_strategy),
        (pe.Float32(), pe.Float64(), get_dataframe_strategy),
        (pe.String(), pe.Categorical(), get_dataframe_strategy),
        (pe.Int16(), pe.String(), get_dataframe_strategy),
    ],
)
@given(st.data())
@settings(max_examples=5)
def test_coerce_cast(from_dtype, to_dtype, strategy, data):
    """Test that dtypes can be coerced with casting."""
    s = data.draw(strategy(from_dtype.type))

    coerced = to_dtype.coerce(data_container=s)
    for dtype in coerced.dtypes:
        assert dtype == to_dtype.type


@pytest.mark.parametrize(
    "pandera_dtype, data_container",
    [
        (
            pe.Decimal(precision=3, scale=2),
            pl.LazyFrame([["1.11111", "2.22222", "3.33333"]]),
        ),
        (
            pe.Category(categories=["a", "b", "c"]),
            pl.LazyFrame([["a", "b", "c"]]),
        ),
    ],
)
def test_coerce_cast_special(pandera_dtype, data_container):
    """Test that dtypes can be coerced with casting."""
    coerced = pandera_dtype.coerce(data_container=data_container)

    for dtype in coerced.dtypes:
        assert dtype == pandera_dtype.type

    if isinstance(pandera_dtype, pe.Decimal):
        # collecting a LazyFrame with decimal type has a bug that casts to
        # pl.Float64
        df = coerced.collect()
        for dtype in df.dtypes:
            assert dtype == pl.Float64


@pytest.mark.parametrize(
    "pl_to_dtype, container, exception_cls",
    [
        (pe.Int8(), pl.LazyFrame({"0": [1000, 100, 200]}), pl.ComputeError),
        (
            pe.Bool(),
            pl.LazyFrame({"0": ["a", "b", "c"]}),
            pl.InvalidOperationError,
        ),
        (pe.Int64(), pl.LazyFrame({"0": ["1", "b"]}), pl.ComputeError),
        (
            pe.Decimal(precision=2, scale=1),
            pl.LazyFrame({"0": [100.11, 2, 3]}),
            pl.ComputeError,
        ),
        (
            pe.Category(categories=["a", "b", "c"]),
            pl.LazyFrame({"0": ["a", "b", "c", "f"]}),
            ValueError,
        ),
    ],
)
def test_coerce_cast_failed(pl_to_dtype, container, exception_cls):
    """Test that dtypes fail when not data is not coercible."""
    with pytest.raises(exception_cls):
        pl_to_dtype.coerce(data_container=container).collect()


@pytest.mark.parametrize(
    "to_dtype, container",
    [
        (pe.Int8(), pl.LazyFrame({"0": [1000, 100, 200]})),
        (pe.Bool(), pl.LazyFrame({"0": ["a", "b", "c"]})),
        (pe.Int64(), pl.LazyFrame({"0": ["1", "b"], "1": ["c", "d"]})),
    ],
)
def test_try_coerce_cast_failed(to_dtype, container):
    """Test that try_coerce() raises ParserError when not coercible."""
    with pytest.raises(pandera.errors.ParserError):
        to_dtype.try_coerce(data_container=container)


@pytest.mark.parametrize("dtype", all_types + special_types)
def test_check_not_equivalent(dtype):
    """Test that check() rejects non-equivalent dtypes."""
    if str(pe.Engine.dtype(dtype)) == "Object":
        actual_dtype = pe.Engine.dtype(int)
    else:
        actual_dtype = pe.Engine.dtype(object)
    expected_dtype = pe.Engine.dtype(dtype)
    assert actual_dtype.check(expected_dtype) is False


@pytest.mark.parametrize("dtype", all_types + special_types)
def test_check_equivalent(dtype):
    """Test that check() accepts equivalent dtypes."""
    actual_dtype = pe.Engine.dtype(dtype)
    expected_dtype = pe.Engine.dtype(dtype)
    assert actual_dtype.check(expected_dtype) is True


@pytest.mark.parametrize(
    "first_dtype, second_dtype, equivalent",
    [
        (pe.Int8, pe.Int16, False),
        (pe.Category(categories=["a", "b"]), pe.String, False),
        (
            pe.Decimal(precision=2, scale=1),
            pe.Decimal(precision=3, scale=2),
            False,
        ),
        (
            pe.Decimal(precision=2, scale=1),
            pe.Decimal(precision=2, scale=1),
            True,
        ),
        (pe.DateTime(), pe.Date, False),
        (
            pe.Category(categories=["a", "b"]),
            pe.Category(categories=["a", "b"]),
            True,
        ),
        (pe.DateTime(time_unit="us"), pe.DateTime(time_unit="ns"), False),
        (pe.DateTime(time_unit="us"), pe.DateTime(time_unit="us"), True),
    ],
)
def test_check_equivalent_custom(first_dtype, second_dtype, equivalent):
    """Test that check() rejects non-equivalent dtypes."""
    first_engine_dtype = pe.Engine.dtype(first_dtype)
    second_engine_dtype = pe.Engine.dtype(second_dtype)
    assert first_engine_dtype.check(second_engine_dtype) is equivalent


@pytest.mark.parametrize(
    "to_dtype, container, result",
    [
        (
            pl.UInt32,
            pl.LazyFrame(
                data={"0": [1000, 100, 200], "1": [1000, 100, 200]},
                schema={"0": pl.Int32, "1": pl.Int32},
            ),
            pl.LazyFrame({COERCIBLE_KEY: [True, True, True]}),
        ),
        (
            pl.Int64,
            pl.LazyFrame(
                data={"0": [1000, 100, 200]},
                schema={"0": pl.Int32},
            ),
            pl.LazyFrame({COERCIBLE_KEY: [True, True, True]}),
        ),
        (
            pl.UInt32,
            pl.LazyFrame(
                data={"0": ["1000", "a", "200"], "1": ["1000", "100", "c"]},
                schema={"0": pl.Utf8, "1": pl.Utf8},
            ),
            pl.LazyFrame({COERCIBLE_KEY: [True, False, False]}),
        ),
        (
            pl.Int64,
            pl.LazyFrame(data={"0": ["d", "100", "200"]}),
            pl.LazyFrame({COERCIBLE_KEY: [False, True, True]}),
        ),
    ],
)
def test_polars_object_coercible(to_dtype, container, result):
    """
    Test that polars_object_coercible can detect that a polars object is
    coercible or not.
    """
    is_coercible = polars_object_coercible(PolarsData(container), to_dtype)
    assert_frame_equal(is_coercible, result)


@pytest.mark.parametrize(
    "inner_dtype_cls",
    [
        pl.Utf8,
        *pl.NUMERIC_DTYPES,
    ],
)
@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=5)
def test_polars_nested_array_type_check(inner_dtype_cls, width):
    polars_dtype = pl.Array(inner_dtype_cls(), width)
    pandera_dtype = pe.Engine.dtype(polars_dtype)

    assert pandera_dtype.check(polars_dtype)
    assert pandera_dtype.check(pandera_dtype)
    assert not pandera_dtype.check(inner_dtype_cls)
    assert not pandera_dtype.check(inner_dtype_cls())


@pytest.mark.parametrize(
    "inner_dtype_cls",
    [
        pl.Utf8,
        *pl.NUMERIC_DTYPES,
    ],
)
def test_polars_list_nested_type(inner_dtype_cls):
    polars_dtype = pl.List(inner_dtype_cls())
    pandera_dtype = pe.Engine.dtype(polars_dtype)

    assert pandera_dtype.check(polars_dtype)
    assert pandera_dtype.check(pandera_dtype)
    assert not pandera_dtype.check(inner_dtype_cls)
    assert not pandera_dtype.check(inner_dtype_cls())


@pytest.mark.parametrize(
    "inner_dtype_cls",
    [
        pl.Utf8,
        *pl.NUMERIC_DTYPES,
    ],
)
def test_polars_struct_nested_type(inner_dtype_cls):
    polars_dtype = pl.Struct({k: inner_dtype_cls() for k in "abc"})
    pandera_dtype = pe.Engine.dtype(polars_dtype)

    assert pandera_dtype.check(polars_dtype)
    assert pandera_dtype.check(pandera_dtype)
    assert not pandera_dtype.check(inner_dtype_cls)
    assert not pandera_dtype.check(inner_dtype_cls())


@pytest.mark.parametrize(
    "coercible_dtype, noncoercible_dtype, data",
    [
        # Array
        [
            pl.Array(pl.Int64(), 2),
            pl.Array(pl.Int64(), 3),
            pl.LazyFrame({"a": [[1, 2], [3, 4]]}),
        ],
        [
            pl.Array(pl.Int32(), 1),
            pl.Array(pl.Int32(), 2),
            pl.LazyFrame({"a": [["1"], ["3"]]}),
        ],
        [
            pl.Array(pl.Float64(), 3),
            pl.Array(pl.Float64(), 5),
            pl.LazyFrame({"a": [[1.0, 2.0, 3.1], [3.0, 4.0, 5.1]]}),
        ],
        # List
        [
            pl.List(pl.Utf8()),
            pl.List(pl.Int64()),
            pl.LazyFrame({"0": [[*"abc"]]}),
        ],
        [
            pl.List(pl.Utf8()),
            pl.List(pl.Boolean()),
            pl.LazyFrame({"0": [[*"xyz"]]}),
        ],
        [
            pl.List(pl.Float64()),
            pl.List(pl.Object()),
            pl.LazyFrame({"0": [[1.0, 2.0, 3.0]]}),
        ],
        # Struct
        [
            pl.Struct({"a": pl.Utf8(), "b": pl.Int64(), "c": pl.Float64()}),
            pl.Struct({"a": pl.Utf8()}),
            pl.LazyFrame({"0": [{"a": "foo", "b": 1, "c": 1.0}]}),
        ],
        [
            pl.Struct({"a": pl.Utf8(), "b": pl.List(pl.Int64())}),
            pl.Struct({"c": pl.Float64()}),
            pl.LazyFrame({"0": [{"a": "foo", "b": [1, 2, 3]}]}),
        ],
        [
            pl.Struct({"a": pl.Array(pl.Int64(), 2), "b": pl.Utf8()}),
            pl.Struct({"d": pl.Utf8()}),
            pl.LazyFrame({"0": [{"a": [1, 2], "b": "foo"}]}),
        ],
    ],
)
def test_polars_nested_dtypes_try_coercion(
    coercible_dtype,
    noncoercible_dtype,
    data,
):
    pandera_dtype = pe.Engine.dtype(coercible_dtype)
    coerced_data = pandera_dtype.try_coerce(PolarsData(data))
    assert coerced_data.collect().equals(data.collect())

    # coercing data with invalid type should raise an error
    try:
        pe.Engine.dtype(noncoercible_dtype).try_coerce(PolarsData(data))
    except pandera.errors.ParserError as exc:
        assert exc.failure_cases.equals(data.collect())
