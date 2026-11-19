"""What a frame of GTA footage can show: the open vocabulary a beat's concept is ranked against.

A frame is accepted for a beat only when, among all of these labels (minus the ones that mean the
same as the beat), the beat's own description is one of the few that fit it best. A seaplane over
the sea is then a "seaplane", not a "jet ski"; a man holding a gun is not "shooting hoops".
"""
from __future__ import annotations

SCENE_LABELS = [
    # vehicles
    "a car", "a sports car", "a muscle car", "a pickup truck", "a police car", "a taxi", "a bus", "a semi truck", "a motorcycle", "a dirt bike",
    "a bicycle", "a quad bike", "a golf cart", "a helicopter", "a police helicopter", "an airplane", "a seaplane", "a private jet", "a jet ski",
    "a speedboat", "a yacht", "a fishing boat", "a sailboat", "a kayak", "a canoe", "an airboat", "a submarine", "a tank", "a monster truck",
    "a lowrider", "a convertible", "an ambulance", "a fire truck", "a tow truck", "a train",
    # driving and action
    "a car chase", "a police chase", "a car crash", "a car drifting", "a street race", "a car jumping", "a burning car", "an explosion",
    "a shootout", "a person shooting a gun", "a person holding a gun", "a person holding a shotgun", "a robbery", "a bank robbery",
    "a store robbery", "a heist crew in masks", "a person being arrested", "police officers with guns", "a SWAT team", "a wanted level on screen",
    "a fist fight", "a person running", "a person jumping", "a person climbing", "a person falling", "a person parachuting",
    # water and nature
    "a person swimming", "a person scuba diving", "a person snorkeling", "a person surfing", "a person fishing", "a person on a jet ski",
    "a person paddling a kayak", "a person driving a boat", "dolphins", "a shark", "a sea turtle", "fish underwater", "a coral reef",
    "an alligator", "a crocodile", "a flamingo", "a pelican", "a bird", "a snake", "a dog", "a cat", "a horse", "a deer", "a wild boar",
    "a swamp", "a mangrove forest", "a river", "a lake", "the open ocean", "a waterfall", "a forest", "a field of grass", "a desert", "mountains",
    "palm trees", "a beach", "a crowded beach", "islands from above", "a bridge over water", "a marina with boats", "a pier", "a lighthouse",
    # sport and body
    "a person playing basketball", "a person shooting hoops", "a basketball hoop", "a basketball court", "a person playing golf", "a person playing tennis", "a person lifting weights",
    "a person boxing", "a punching bag", "a gym", "a person working out", "a person doing push-ups", "a person jogging", "a person skateboarding",
    "a muscular man", "an overweight man", "a person eating fast food", "a burger", "a person drinking", "a person smoking", "a person dancing",
    "a tattoo parlor", "a barber shop", "a person getting a haircut", "a person changing clothes", "a clothing store",
    # people
    "a man", "a woman", "a couple", "a man and a woman", "a group of friends", "a crowd of people", "a party", "a pool party", "a nightclub",
    "a strip club", "a bar", "a concert", "people at a festival", "a person taking a selfie", "a person filming with a phone",
    "a person looking at a phone", "a phone screen", "a social media app", "a livestream on a phone", "a person talking on the phone",
    "a close-up of a face", "a person wearing sunglasses", "a person in a mask", "a police officer", "a security guard", "a gang",
    "a person in a car", "a person driving", "a person sitting", "a person sleeping", "a person in a bikini", "a person in a hoodie",
    # places
    "a city street", "a city at night", "a neon-lit street", "a city skyline", "a highway", "a parking lot", "a gas station", "a convenience store",
    "a supermarket", "a shopping mall", "a restaurant", "a diner", "a hotel", "a motel", "a mansion", "a house", "a house interior", "a kitchen",
    "a bedroom", "a bathroom", "an apartment building", "a trailer park", "a garage", "a car dealership", "a warehouse", "a construction site",
    "a factory", "a port with containers", "an airport", "a prison", "a police station", "a hospital", "a courthouse", "a church",
    "a bank", "an office", "a casino", "a stadium", "a theme park", "a carnival", "a farm", "a small town", "a rooftop", "an alley",
    "a bridge", "a tunnel", "a road through the countryside", "the Vice City sign", "a billboard",
    # light and screens
    "a sunset", "a sunrise", "a night sky", "rain", "a storm", "fog", "fireworks",
    "a game menu", "a map screen", "a loading screen", "a black screen", "text on a screen", "a logo", "a video game heads-up display",
]


STOP = {"a", "an", "the", "of", "with", "in", "on", "at", "to", "and", "or", "person", "people", "some", "his", "her", "their", "your", "you", "it", "is", "are", "be", "from", "for", "by", "into", "through"}


def stem(word: str) -> str:
    w = "".join(ch for ch in word.lower() if ch.isalnum())
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-2] if w.endswith("es") and len(w) > 4 and w[-3] in "sxz" else w[:-1]
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def content(text: str) -> set[str]:
    return {stem(w) for w in str(text).replace("-", " ").split() if stem(w) and w.lower().strip(".,:;!?") not in STOP}


def synonyms(texts: list[str]) -> list[bool]:
    """Labels that ARE the concept (all their content words are in it): they are not its competitors."""
    words = set().union(*(content(t) for t in texts)) if texts else set()
    return [bool(content(label)) and content(label) <= words for label in SCENE_LABELS]
