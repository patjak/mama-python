from __future__ import annotations

from typing import Any, Dict, List, Union

from logger import debug, fatal
from services import Util


class Options:
    options: Dict[str, Any] = {}

    @classmethod
    def parse(cls, args: List[str], opts: List[str]) -> List[str]:
        """
        Custom command-line options parser matching the MAMA PHP argument logic.
        Extracts '--' options based on the definition list 'opts', populates
        Options.options, and returns remaining positional arguments/commands.
        """
        remaining_args = list(args)
        i = 1

        while i < len(remaining_args):
            arg = remaining_args[i]

            if not arg.startswith("--"):
                i += 1
                continue

            matched = False
            for opt_def in opts:
                if opt_def.endswith(":"):
                    # Parse options that require a parameter (e.g., "args:", "packages:")
                    opt_name = opt_def[:-1]
                    val = Util.parse_after(f"--{opt_name}", arg)

                    if val is False or val is None:
                        continue

                    if val == "":
                        # Option provided as separate tokens: --option value
                        if i + 1 < len(remaining_args):
                            cls.options[opt_name] = remaining_args[i + 1]
                            remaining_args.pop(i + 1)
                            remaining_args.pop(i)
                            matched = True
                            break
                    elif val.startswith("="):
                        # Option provided in inline format: --option=value
                        cls.options[opt_name] = val[1:]
                        remaining_args.pop(i)
                        matched = True
                        break
                else:
                    # Parse boolean flag options without parameters (e.g., "debug", "no-retry")
                    opt_name = opt_def
                    if arg == f"--{opt_name}":
                        cls.options[opt_name] = True
                        remaining_args.pop(i)
                        matched = True
                        break

            if not matched:
                i += 1

        return remaining_args

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