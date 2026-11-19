from dataclasses import dataclass

from hypothesis import strategies as st


@dataclass
class Point:
    x: object
    y: object


scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False),
    st.text(max_size=8),
)

hashables = st.recursive(
    scalars,
    lambda inner: st.one_of(st.tuples(inner, inner), st.frozensets(inner, max_size=3)),
    max_leaves=6,
)

values = st.recursive(
    scalars,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4),
        st.lists(inner, max_size=4).map(tuple),
        st.sets(hashables, max_size=4),
        st.frozensets(hashables, max_size=4),
        st.dictionaries(hashables, inner, max_size=4),
        st.builds(Point, inner, inner),
    ),
    max_leaves=20,
)
