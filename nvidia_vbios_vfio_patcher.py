#!/usr/bin/env python3
"""
Convert a full NVIDIA vBIOS ROM into a form compatible for PCI passthrough.
This version uses strict hex-safe regex patterns ([a-f0-9]) and includes a
few small robustness fixes while preserving original behavior.
"""
import sys
import binascii
import argparse
import re


CONFIRM_TEXT = f"I agree to be careful"

WARNING_TEXT = f"""
USE THIS SOFTWARE AT YOUR OWN DISCRETION. THIS SOFTWARE HAS *NOT* BEEN
EXTENSIVELY TESTED AND MAY NOT WORK WITH YOUR GRAPHICS CARD.

If you want to save the created vBIOS file, type the following phrase
EXACTLY as it is written below:

{CONFIRM_TEXT}
"""


class CheckException(Exception):
    pass


class VBIOSROM:
    def __init__(self, f):
        """
        Load a VBIOS and convert it into a hex-ascii format for easier editing.
        `self.content` is the hex ascii bytes returned by binascii.hexlify.
        """
        content = f.read()
        self.offsets = {"header": None, "footer": None}
        self.content = binascii.hexlify(content)  # bytes containing lowercase hex

    def detect_offsets(self, disable_footer=False):
        """
        Search the ROM for known sections of data and raise a CheckException
        if any of the required markers can't be found.
        """
        if not isinstance(self.content, (bytes, bytearray)):
            raise CheckException("Internal error: content is not bytes")

        # Header regex — still structured to yield 6 groups (same as original).
        HEADER_REGEX = (
            b'55aa(([a-f0-9]){2})(eb)(([a-f0-9]){20})(564944454f)'
        )
        header_match = re.search(HEADER_REGEX, self.content)
        if not header_match or len(header_match.groups()) != 6:
            raise CheckException("Couldn't find the ROM header!")
        self.offsets["header"] = header_match.start()

        if not disable_footer:
            # Footer detectors: [a-f0-9] used strictly for hex digits.
            FOOTER_DETECTORS = [
                (b'564e(([a-f0-9]){636})(4e504453)(([a-f0-9]){56})(4e504445)', 'RTX 30XX'),
                (b'564e(([a-f0-9]){572})(4e504453)(([a-f0-9]){56})(4e504445)', 'RTX 2060'),
                (b'564e(([a-f0-9]){476})(4e504453)(([a-f0-9]){56})(4e504445)', 'GTX 16XX / RTX 20XX'),
                (b'564e(([a-f0-9]){444})(4e504453)(([a-f0-9]){56})(4e504445)', 'Quadro PXXX'),
                (b'564e(([a-f0-9]){348})(4e504453)(([a-f0-9]){56})(4e504445)', 'GTX 10XX'),
                (b'564e(([a-f0-9]){188})(4e504453)(([a-f0-9]){56})(4e504445)', 'GTX 980'),
                (b'564e(([a-f0-9]){124})(4e504453)(([a-f0-9]){56})(4e504445)', 'GTX 400 - 900 Series'),
            ]

            for pattern, series in FOOTER_DETECTORS:
                m = re.search(pattern, self.content)
                if m and len(m.groups()) == 6:
                    self.offsets["footer"] = m.start()
                    print(f"ROM footer for {series} found!")
                    return

            raise CheckException("Couldn't find the ROM footer!")

    def run_sanity_tests(self, ignore_check=False):
        """
        Run sanity tests between header and footer:
        - exactly one NPDS (4e504453)
        - exactly three NPDE (4e504445)
        - exactly two NPDE after NPDS
        """
        if self.offsets["header"] is None or self.offsets["footer"] is None:
            raise CheckException("Header/footer offsets not set before sanity checks")

        header = self.offsets["header"]
        footer = self.offsets["footer"]

        try:
            npds_count = self.content.count(b"4e504453", header, footer)
            if npds_count != 1:
                raise CheckException(
                    f"Expected only one 'NPDS' marker between header and footer, found {npds_count}"
                )

            npde_count = self.content.count(b"4e504445", header, footer)
            if npde_count != 3:
                raise CheckException(
                    f"Expected three 'NPDE' markers between header and footer, found {npde_count} (possible vBIOS without UEFI support)"
                )

            npds_pos = self.content.find(b"4e504453", header, footer)
            if npds_pos == -1:
                raise CheckException("Couldn't find NPDS position within header/footer bounds")

            npde_after_npds_count = self.content.count(b"4e504445", npds_pos, footer)
            if npde_after_npds_count != 2:
                raise CheckException("Expected two 'NPDE' markers after the 'NPDS' marker")
        except CheckException as e:
            if ignore_check:
                print(f"Encountered error during sanity check: {e}")
                print("Ignoring...")
                return
            else:
                raise

        print("No problems found.")

    def get_spliced_rom(self, disable_footer=False):
        """
        Return binary data constructed from the spliced hex-ascii content.
        Start from header; if footer present and not disabled, end at footer.
        """
        if self.offsets["header"] is None:
            raise CheckException("Header offset not found; cannot splice ROM")

        start = self.offsets["header"]
        if not disable_footer:
            if self.offsets["footer"] is None:
                raise CheckException("Footer offset not found; cannot splice ROM")
            end = self.offsets["footer"]
            spliced = self.content[start:end]
        else:
            spliced = self.content[start:]

        # Convert hex ascii bytes back into binary
        try:
            return binascii.unhexlify(spliced)
        except (binascii.Error, TypeError) as e:
            raise CheckException(f"Failed to unhexlify spliced content: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert a full NVIDIA vBIOS ROM into a form compatible for PCI passthrough."
    )
    parser.add_argument("-i", type=str, required=True, help="The full ROM to read")
    parser.add_argument("-o", type=str, required=True, help="Path for saving the newly generated ROM")
    parser.add_argument("--ignore-sanity-check", default=False, action="store_true",
                        help="Don't halt the script if any of the sanity checks fails")
    parser.add_argument("--disable-footer-strip", default=False, action="store_true",
                        help="Don't strip the footer from the vBIOS (Allows you to convert older gen GPUs)")
    parser.add_argument("--skip-the-very-important-warning", default=False, action="store_true",
                        help="Skip the very important warning and save the ROM without asking for any input.")

    args = parser.parse_args()

    print("Opening the ROM file...")
    with open(args.i, "rb") as f:
        rom = VBIOSROM(f)

    print("Scanning for ROM offsets...")
    rom.detect_offsets(args.disable_footer_strip)
    print("Offsets found!")

    if not args.disable_footer_strip:
        print("Running sanity checks...")
        rom.run_sanity_tests(args.ignore_sanity_check)

    spliced_rom = rom.get_spliced_rom(args.disable_footer_strip)

    if not args.skip_the_very_important_warning:
        print(WARNING_TEXT)
        answer = input("Type here: ")
        if answer != CONFIRM_TEXT:
            print("Wrong answer, halting...")
            sys.exit(1)

    print("Writing the edited ROM...")
    with open(args.o, "wb") as f:
        f.write(spliced_rom)

    print("Done!")


if __name__ == "__main__":
    main()
