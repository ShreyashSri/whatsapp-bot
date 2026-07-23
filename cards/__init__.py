"""Card renderer — generates achievement cards as PNG and PDF.

Provides render_card() and the CARD_TYPES list so other modules can validate
user input without importing internals.
"""

from cards.render import render_card, CARD_TYPES, CARD_W, CARD_H

__all__ = ["render_card", "CARD_TYPES", "CARD_W", "CARD_H"]
