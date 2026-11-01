"""Plain-language "Why am I seeing this?" explanations for delivered reels."""


def feed_label(name: str) -> str:
    return name.replace("_", " ").title()


def _feeds_phrase(names) -> str:
    labels = [feed_label(name) for name in sorted(names)]
    if len(labels) == 1:
        return f"your {labels[0]} feed"
    return "your " + ", ".join(labels[:-1]) + f" and {labels[-1]} feeds"


def _feature_phrase(token: str) -> str:
    kind, _, value = token.partition(":")
    if kind == "tag":
        return f"#{value}"
    if kind == "word":
        return f"the word “{value}”"
    if kind == "audio":
        return "the same audio"
    if kind == "song":
        return f"the song “{value.split(':', 1)[0]}”"
    return value


def _join(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


LEAD_TEXT = {
    "coauthor": "collaborated on a reel by @{via}",
    "sponsored_coauthor": "collaborated on a sponsored reel by @{via}",
    "audio_creator": "created original audio used in a reel by @{via}",
    "sponsored_audio_creator": "created original audio used in a sponsored reel by @{via}",
    "credit": "was credited in a reel by @{via}",
    "mention": "was tagged or mentioned in a reel by @{via}",
    "sponsored_credit": "was credited in a sponsored reel by @{via}",
    "sponsored_mention": "was mentioned in a sponsored reel by @{via}",
}


class Reason:
    """Collects why one reel was chosen, then renders it for the reels view."""

    def __init__(self, owner: str):
        self.owner = owner
        self.saved_in: set[str] = set()
        self.lead: dict | None = None
        self.liked_creator = False
        self.starter = False
        self.topics: dict[str, str] = {}  # topic -> 'bio' | 'captions'
        self.uncertain = False
        self.exploratory = False
        self.features: list[str] = []
        self.view_boost = False

    def source_details(self) -> list[str]:
        owner = f"@{self.owner}"
        if self.saved_in:
            return [f"{owner} is saved in {_feeds_phrase(self.saved_in)}."]
        details = []
        if self.lead and self.lead.get("source") == "hashtag":
            tags = ", ".join("#" + tag for tag in self.lead.get("tags") or [])
            details.append(f"{owner} uses hashtags from reels in your feeds: {tags}." if tags
                           else f"{owner} uses hashtags from reels in your feeds.")
        elif self.lead and self.lead.get("source") in LEAD_TEXT:
            text = f"{owner} " + LEAD_TEXT[self.lead["source"]].format(via=self.lead.get("via") or "another creator")
            details.append(text + (", which you liked." if self.lead.get("liked") else "."))
        if self.liked_creator:
            details.append(f"You liked a reel from {owner} before.")
        if self.starter:
            details.append(f"{owner} is on the built-in list of public meme pages to try.")
        if not details:
            details.append(f"{owner} was found through profiles and reels you opened.")
        return details

    def topic_details(self) -> list[str]:
        if self.saved_in:
            return []
        if self.uncertain:
            return ["Its topic couldn’t be confirmed, so this is an occasional trial. Press N if it’s off."]
        details = []
        for kind in ("bio", "captions"):
            names = [name for name, found in self.topics.items() if found == kind]
            if names:
                where = "Its bio" if kind == "bio" else "Its recent captions"
                details.append(f"{where} match{'es' if kind == 'bio' else ''} {_feeds_phrase(names)}.")
        return details

    def pick_details(self) -> list[str]:
        if self.exploratory:
            return ["This reel was picked at random to keep your feed varied."]
        details = []
        if self.view_boost:
            details.append("More views than the median of this creator’s loaded reels gave it a small ranking boost.")
        if self.features:
            return details + [f"Picked because it shares {_join(_feature_phrase(t) for t in self.features)} "
                    "with reels you liked."]
        return details + [f"Chosen from @{self.owner}’s reels you haven’t seen yet."]

    def summary(self) -> str:
        if self.saved_in:
            return "From " + _feeds_phrase(self.saved_in)
        if self.uncertain:
            return "Trying a new creator"
        if self.liked_creator and not self.lead:
            return "From a creator you liked"
        return "New creator for you"

    def as_dict(self) -> dict:
        return {"summary": self.summary(),
                "details": self.source_details() + self.topic_details() + self.pick_details()}
