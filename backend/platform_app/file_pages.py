"""Bounded reverse reads for append-only control-plane projections."""


def reverse_lines(path, chunk_size=65536):
    if not path.is_file():
        return
    with path.open('rb') as stream:
        position = stream.seek(0, 2)
        pending = b''
        while position:
            count = min(chunk_size, position)
            position -= count
            stream.seek(position)
            parts = (stream.read(count) + pending).split(b'\n')
            pending = parts[0]
            for line in reversed(parts[1:]):
                if line:
                    yield line
        if pending:
            yield pending
