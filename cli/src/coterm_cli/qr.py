from __future__ import annotations

def render_ascii_qr(payload: str) -> str:
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    return "\n".join("".join("██" if cell else "  " for cell in row) for row in matrix)
