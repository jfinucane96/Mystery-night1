"""The automatic checks run against a fixed placeholder story (tests/fixtures),
so writing the real party story in content/story.json never breaks them."""

import os

os.environ.setdefault("MM_STORY", os.path.join(os.path.dirname(__file__), "fixtures", "story_placeholder.json"))
