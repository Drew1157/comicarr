#  Copyright (C) 2026 Comicarr contributors
#
#  This file is part of Comicarr.
#
#  Comicarr is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

"""Launch Comicarr with the custom organization extension installed."""

from comicarr_custom.bootstrap import install


def main():
    install()

    import Comicarr

    Comicarr.main()


if __name__ == "__main__":
    main()
