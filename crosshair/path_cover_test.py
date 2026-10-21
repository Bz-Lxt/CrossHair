import asyncio
import functools
import re
import textwrap
from dataclasses import dataclass
from enum import Enum
from io import StringIO
from typing import Callable, Optional

from crosshair.fnutil import FunctionInfo
from crosshair.options import DEFAULT_OPTIONS
from crosshair.path_cover import (
    CoverageType,
    output_eval_exression_paths,
    output_pytest_paths,
    path_cover,
)
from crosshair.statespace import context_statespace
from crosshair.tracers import NoTracing


def _foo(x: int) -> int:
    if x > 100:
        return 100
    return x


def _regex(x: str) -> bool:
    compiled = re.compile("f(o)+")
    return bool(compiled.fullmatch(x))


def _exceptionex(x: int) -> int:
    if x == 42:
        raise ValueError
    return x


def _symbolic_exception_example(x: str) -> str:
    if x == "foo'bar\"baz":
        raise ValueError(x)
    return x


def _has_no_successful_paths(x: int) -> None:
    with NoTracing():
        context_statespace().defer_assumption("fail", lambda: False)


async def _async_branching_example(x: int) -> int:
    await asyncio.sleep(0)
    if x > 0:
        return x * 2
    elif x == 0:
        raise KeyError("zero")
    else:
        return -x


@dataclass
class Train:
    class Color(Enum):
        RED = 0

    color: Color


def _paint_train(train: Train, color: Train.Color) -> Train:
    return Train(color=color)


OPTS = DEFAULT_OPTIONS.overlay(max_iterations=10)
foo = FunctionInfo.from_fn(_foo)
decorated_foo = FunctionInfo.from_fn(functools.lru_cache()(_foo))
regex = FunctionInfo.from_fn(_regex)
exceptionex = FunctionInfo.from_fn(_exceptionex)
symbolic_exception_example = FunctionInfo.from_fn(_symbolic_exception_example)
has_no_successful_paths = FunctionInfo.from_fn(_has_no_successful_paths)
paint_train = FunctionInfo.from_fn(_paint_train)


def test_path_cover_foo() -> None:
    paths = list(path_cover(foo, OPTS, CoverageType.OPCODE))
    assert len(paths) == 2
    small, large = sorted(paths, key=lambda p: p.result)  # type: ignore
    assert large.result == "100"
    assert large.args.arguments["x"] > 100
    assert small.result == repr(small.args.arguments["x"])


def test_path_cover_decorated_foo() -> None:
    paths = list(path_cover(decorated_foo, OPTS, CoverageType.OPCODE))
    assert len(paths) == 2


def test_path_cover_regex() -> None:
    paths = list(path_cover(regex, OPTS, CoverageType.OPCODE))
    assert len(paths) == 1
    paths = list(path_cover(regex, OPTS, CoverageType.PATH))
    input_output = set((p.args.arguments["x"], p.result) for p in paths)
    assert ("foo", "True") in input_output


def test_path_cover_exception_example() -> None:
    paths = list(path_cover(exceptionex, OPTS, CoverageType.OPCODE))
    out, err = StringIO(), StringIO()
    output_eval_exression_paths(_exceptionex, paths, out, err)
    assert "_exceptionex(42)" in out.getvalue()


def test_path_cover_symbolic_exception_message() -> None:
    paths = list(path_cover(symbolic_exception_example, OPTS, CoverageType.OPCODE))
    _imports, lines = output_pytest_paths(_symbolic_exception_example, paths)
    expected = textwrap.dedent("""\
        def test__symbolic_exception_example():
            with pytest.raises(ValueError, match='foo\\'bar"baz'):
                _symbolic_exception_example('foo\\'bar"baz')""")
    assert expected in "\n".join(lines)


def test_path_cover_async_example() -> None:
    paths = list(
        path_cover(
            FunctionInfo.from_fn(_async_branching_example),
            OPTS,
            CoverageType.OPCODE,
        )
    )
    assert len(paths) == 3
    outcomes = {(p.args.arguments["x"], p.result, p.exc) for p in paths}
    assert (-1, "1", None) in outcomes
    assert (1, "2", None) in outcomes
    assert (0, "None", KeyError) in outcomes
    exc_paths = [p for p in paths if p.exc is not None]
    assert len(exc_paths) == 1
    assert exc_paths[0].exc_message == "'zero'"
    imports, lines = output_pytest_paths(_async_branching_example, paths)
    text = "\n".join(lines)
    assert "import asyncio" in imports
    assert "asyncio.run(_async_branching_example(" in text
    assert "pytest.raises(KeyError" in text


def test_has_no_successful_paths() -> None:
    assert list(path_cover(has_no_successful_paths, OPTS, CoverageType.OPCODE)) == []


def test_path_cover_lambda() -> None:
    def lambdaFn(a: Optional[Callable[[int], int]]):
        if a:
            return a(2) + 4
        else:
            return "hello"

    assert path_cover(FunctionInfo.from_fn(lambdaFn), OPTS, CoverageType.OPCODE)
    # TODO: more detailed assert?


def test_path_cover_pytest_output() -> None:
    paths = list(path_cover(paint_train, OPTS, CoverageType.OPCODE))
    imports, lines = output_pytest_paths(_paint_train, paths)
    assert lines == [
        "def test__paint_train():",
        "    assert _paint_train(Train(Train.Color.RED), Train.Color.RED) == Train(color=Train.Color.RED)",
        "",
    ]
    assert imports == {
        "from crosshair.path_cover_test import _paint_train",
        "from crosshair.path_cover_test import Train",
    }
