from __future__ import annotations


class DummySession:
    pass


def test_image_context_accepts_prebaked_dict():
    from contexts.image_context import ImageContext
    sess = DummySession()
    data = {
        'name': 'pic.png',
        'content': 'YmFzZTY0ZGF0YQ==',
        'mime_type': 'image/png',
        'source_type': 'base64',
    }
    ctx = ImageContext(sess, data)
    out = ctx.get()
    assert out['name'] == 'pic.png'
    assert out['mime_type'] == 'image/png'
    assert out['content'] == 'YmFzZTY0ZGF0YQ=='
    assert out['source_type'] == 'base64'

