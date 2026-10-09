from __future__ import annotations

from typing import Any, Dict, List, Union

from logger import debug, fatal


class Options:
    options: Dict[str, Any] = {}

    @classmethod
    def parse(cls, args: List[str], opts: List[str]) -> List[str]:
        """
        Legacy entry point maintained for backwards compatibility.
        Standard argparse in main.py populates Options.options directly.
        """
        return args

    @classmethod
    def get(cls, opt: str, required: bool = True) -> Union[Any, bool]:
        """
        Retrieve an option provided by the user on the command line.
        """
        if opt in cls.options:
            val = cls.options[opt]
            debug(f"Option from command line: {opt} = {val}")
            return val

        if required:
            fatal(f"Couldn't get required option: {opt}")

        return False