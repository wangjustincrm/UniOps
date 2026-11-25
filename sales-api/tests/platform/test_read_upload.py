"""What a person sends is read at most a byte past the limit (platform/files.read_upload)."""
from app.platform.files import read_upload


class Sent:
    def __init__(self, size: int) -> None:
        self.size, self.asked = size, []

    async def read(self, n: int = -1) -> bytes:
        self.asked.append(n)
        return b"x" * (self.size if n < 0 else min(n, self.size))


async def test_a_file_within_the_limit_is_whole_and_a_larger_one_is_a_byte_past_it():
    for size, got in ((0, 0), (10, 10), (11, 11), (10_000_000, 11)):
        f = Sent(size)
        assert len(await read_upload(f, 10)) == got and f.asked == [11]
