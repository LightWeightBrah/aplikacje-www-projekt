"""Kanały Magenta TV w pakietach S / M / L – z oficjalnej listy T-Mobile
„Lista programów Magenta TV”, stan na dzień 1.07.2026 r. (t-mobile.pl). Pakiety się sumują:
M = S + kanały z M, L = S + M + kanały z L. Liczby kanałów jak na t-mobile.pl: 85 / 170 / 207
(T-Mobile liczy też regionalne TVP 3 i kanały w kilku kategoriach). Listę aktualizuje manager:
Taryfy i produkty → Edytuj → „Informacje dla agenta” (albo plik taryf)."""

CHANNELS_AS_OF = "1.07.2026"
CHANNEL_COUNTS = {"S": 85, "M": 170, "L": 207}

CHANNELS = {
    "S": (
        "Ogólne: TVP 1, TVP 2, TVP 3 (regionalne), Polsat, TVN, TVP Info, TV4, TV6, Super Polsat, TVN Siedem, TV Puls, Puls 2, TVP Polonia, TVP Rozrywka, TVP Sport, TVP Kultura, TVP Historia, TVP Dokument, TVP Kobieta, TVP ABC, Metro, Nowa TV, WP, TVP World, TVS, TVP Nauka, Alfa TVP, Tele 5, Polonia 1, Antena HD, Home TV, TVC, Kabaret TV, Pogoda 24 TV, Telewizja Republika, ZERO",
        "Filmy i seriale: FX, Paramount Network, Comedy Central, Stopklatka, Zoom TV, CTV − DLA CIEBIE, TVC Super, Kino Polska",
        "Informacje: Polsat News, Wydarzenia 24, Polsat News Polityka, DW English, FRANCE 24 French, FRANCE 24 English",
        "Lifestyle: TTV, TBN Polska HD, Xtreme TV",
        "Dokument: Fokus, Adventure, Show TV, ViDoc TV",
        "Muzyka: Eska TV, Eska TV Extra, Polo TV, Kino Polska Muzyka, 4Fun TV, 4Fun Dance, Power TV, Nuta TV, Nuta Gold, Szlagier TV",
        "Dzieci: Nick Jr., Nickelodeon, Da Vinci, 4Fun Kids",
    ),
    "M": (
        "Ogólne: TVN24, Polsat 2",
        "Filmy i seriale: Polsat Film, Polsat Seriale, Polsat Rodzina, FX Comedy, TVN Fabuła, Polsat Comedy Central Extra, TVP Seriale, TVP HD, Romance TV HD, Warner TV, Polsat Film 2",
        "Informacje: Polsat News 2, TVN24 BiS, BBC World News, CNN, Republika",
        "Sport: Eurosport 1, Eurosport 2, Polsat Sport 1, Polsat Sport 3, Polsat Sport 2, Polsat Sport Fight, Polsat Games, TVN Turbo, DTX, EUROSPORT 3, EUROSPORT 4, Golf Zone",
        "Lifestyle: HGTV, TVN Style, TLC, Food Network, Discovery Life, Polsat Café, Polsat Play, BBC Lifestyle, CANAL+ KUCHNIA, CANAL+ DOMO, Polsat X, Polsat Reality",
        "Dokument: Polsat Doku, Discovery Channel, Discovery Science, Discovery Historia SD, Animal Planet, Travel Channel, ID Investigation Discovery, BBC Earth, Planete+, National Geographic, National Geographic Wild, History HD",
        "Muzyka: Eska Rock, VOX Music TV, Disco Polo Music, Polsat Music, MTV Polska",
        "Dzieci: Disney Channel, Disney Junior, NickToons, MiniMini+, Cartoon Network, Top Kids, Junior Music, Junior Channel",
    ),
    "L": (
        "Filmy i seriale: HBO HD, HBO 2 HD, HBO 3 HD, BBC First, BBC Brit, AleKino+, Novelas+, AXN HD, AXN Black, AXN Spin, AXN White, AMC, Epic Drama HD, Filmax 4K",
        "Sport: Eleven Sports 1, Eleven Sports 2, Eleven Sports 3, Eleven Sports 4, Eleven Sports 1 4K, CANAL+ Sport 5, Motowizja",
        "Lifestyle: Active Family, Fashion TV 4K, MyZen.TV 4K",
        "Dokument: Nat Geo People, History 2 HD, Crime&Investigation Polsat, Love Nature 4K, TravelXP 4K, Museum 4K, Polsat Viasat History HD, Polsat Viasat Explore HD, Polsat Viasat Nature HD, ViDoc TV 4K",
        "Muzyka: Music Box, STARS.TV, Mezzo HD, Ultra TV 4K",
        "Dzieci: Disney XD, TeenNick, tele TOON+, BBC CBeebies, ducktv, Cartoonito",
    ),
}

PLANS = ("S", "M", "L")


def own_groups(plan):
    """Kanały, które dochodzą w tym pakiecie: [(kategoria, [kanały])]."""
    out = []
    for line in CHANNELS[plan]:
        group, names = line.split(":", 1)
        out.append((group.strip(), [c.strip() for c in names.split(",") if c.strip()]))
    return out


def all_groups(plan):
    """WSZYSTKIE kanały pakietu (M = S + M, L = S + M + L), połączone w kategorie:
    agent widzi w L też np. Nickelodeon z pakietu S, a nie tylko kanały dochodzące w L."""
    groups = {}
    for p in PLANS[:PLANS.index(plan) + 1]:
        for group, names in own_groups(p):
            groups.setdefault(group, []).extend(n for n in names if n not in groups.get(group, []))
    return list(groups.items())


# Pakiet dodatkowy (za dopłatą, osobno od S / M / L)
CANAL_PLUS_SUPER_SPORT = (
    "CANAL+ PREMIUM",
    "CANAL+ FILM",
    "CANAL+ 360",
    "CANAL+ SERIALE",
    "CANAL+ 1",
    "CANAL+ 4K",
    "CANAL+ SPORT",
    "CANAL+ SPORT 2",
    "CANAL+ SPORT 3",
    "CANAL+ SPORT 4",
    "CANAL+ EXTRA 1",
    "CANAL+ EXTRA 2",
    "CANAL+ EXTRA 3",
    "CANAL+ EXTRA 4",
    "CANAL+ EXTRA 5",
    "CANAL+ EXTRA 6",
    "CANAL+ EXTRA 7",
    "CANAL+ NOW",
    "CANAL+ DOKUMENT",
)
