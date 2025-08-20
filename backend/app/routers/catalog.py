from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from ..schemas.catalog import PokemonCatalog
from ..mongo import get_mongo_db, mongo_enabled, MONGODB_COLLECTION

router = APIRouter()

# In-memory fallback storage
_CATALOG: Optional[PokemonCatalog] = None


def _seed_pokemon_catalog() -> PokemonCatalog:
    # Rarities
    rarities = [
        "Promo", "Common", "Uncommon", "Rare", "Rare Holo", "Rare Holo EX",
        "Rare Holo GX", "Rare Holo Lv.X", "Rare Prime", "Double Rare", "Ultra Rare",
        "Radiant Rare", "Art Rare", "Special Art Rare", "Shiny Rare", "Shiny Ultra Rare",
        "Hyper Rare", "Black White Rare", "ACE SPEC Rare", "Rare BREAK", "LEGEND", "Amazing",
    ]

    # Languages
    languages = [
        "Japanese", "English", "Korean", "French", "German", "Italian", "Russian",
        "Portuguese", "Spanish", "Simplified Chinese", "Traditional Chinese", "Thai", "Dutch",
    ]

    # English series and sets
    english_series = [
        {"name": "Original", "sets": [
            "Base Set", "Jungle", "Fossil", "Base Set 2", "Team Rocket", "Gym Heroes", "Gym Challenge"
        ]},
        {"name": "Neo", "sets": [
            "Neo Genesis", "Neo Discovery", "Neo Revelation", "Neo Destiny"
        ]},
        {"name": "E-Card", "sets": [
            "Expedition Base Set", "Aquapolis", "Skyridge"
        ]},
        {"name": "EX", "sets": [
            "EX Ruby & Sapphire", "EX Sandstorm", "EX Dragon", "EX Team Magma vs Team Aqua",
            "EX Hidden Legends", "EX FireRed & LeafGreen", "EX Team Rocket Returns", "EX Deoxys",
            "EX Emerald", "EX Unseen Forces", "EX Delta Species", "EX Legend Maker", "EX Holon Phantoms",
            "EX Crystal Guardians", "EX Dragon Frontiers", "EX Power Keepers"
        ]},
        {"name": "Diamond & Pearl", "sets": [
            "Diamond & Pearl", "Diamond & Pearl: Mysterious Treasures", "Diamond & Pearl: Secret Wonders",
            "Diamond & Pearl: Great Encounters", "Diamond & Pearl: Majestic Dawn", "Diamond & Pearl: Legends Awakened",
            "Diamond & Pearl: Stormfront"
        ]},
        {"name": "POP", "sets": [
            "POP Series 1", "POP Series 2", "POP Series 3", "POP Series 4", "POP Series 5", "POP Series 6",
            "POP Series 7", "POP Series 8", "POP Series 9"
        ]},
        {"name": "Platinum", "sets": [
            "Platinum", "Platinum: Rising Rivals", "Platinum: Supreme Victors", "Platinum: Arceus"
        ]},
        {"name": "HeartGold & SoulSilver", "sets": [
            "HeartGold & SoulSilver", "HeartGold & SoulSilver: Unleashed", "HeartGold & SoulSilver: Undaunted",
            "HeartGold & SoulSilver: Triumphant"
        ]},
        {"name": "Black & White", "sets": [
            "Black & White", "Black & White: Emerging Powers", "Black & White: Noble Victories", "Black & White: Next Destinies",
            "Black & White: Dark Explorers", "Black & White:  Dragons Exalted", "Black & White: Boundaries Crossed",
            "Black & White: Plasma Storm", "Black & White: Plasma Freeze", "Black & White: Plasma Blast",
            "Black & White: Legendary Treasures"
        ]},
        {"name": "XY", "sets": [
            "XY", "XY: Kalos Starter Set", "XY: Flashfire", "XY: Furious Fists", "XY: Phantom Forces",
            "XY: Primal Clash", "XY: Roaring Skies", "XY: Ancient Origins", "XY: BREAKthrough", "XY: BREAKpoint",
            "XY: Fates Collide", "XY: Steam Siege", "XY: Evolutions"
        ]},
        {"name": "Sun & Moon", "sets": [
            "Sun & Moon", "Sun & Moon: Guardians Rising", "Sun & Moon: Burning Shadows", "Sun & Moon: Shining Legends",
            "Sun & Moon: Crimson Invasion", "Sun & Moon: Ultra Prism", "Sun & Moon: Forbidden Light", "Sun & Moon: Celestial Storm",
            "Sun & Moon: Lost Thunder", "Sun & Moon: Team Up", "Sun & Moon: Unbroken Bonds", "Sun & Moon: Unified Minds",
            "Sun & Moon: Cosmic Eclipse"
        ]},
        {"name": "Sword & Shield", "sets": [
            "Sword & Shield", "Sword & Shield: Rebel Clash", "Sword & Shield: Darkness Ablaze", "Sword & Shield: Vivid Voltage",
            "Sword & Shield: Battle Styles", "Sword & Shield: Chilling Reign", "Sword & Shield: Evolving Skies",
            "Sword & Shield: Fusion Strike", "Sword & Shield: Brilliant Stars", "Sword & Shield: Astral Radiance",
            "Sword & Shield: Lost Origin", "Sword & Shield: Silver Tempest"
        ]},
        {"name": "Scarlet & Violet", "sets": [
            "Scarlet & Violet", "Scarlet & Violet: Paldea Evolved", "Scarlet & Violet: Obsidian Flames", "Scarlet & Violet: 151",
            "Scarlet & Violet: Paradox Rift", "Scarlet & Violet: Paldean Fates", "Scarlet & Violet: Temporal Forces",
            "Scarlet & Violet: Twilight Masquerade", "Scarlet & Violet: Shrouded Fable", "Scarlet & Violet: Stellar Crown",
            "Scarlet & Violet: Surging Sparks", "Scarlet & Violet: Prismatic Evolutions", "Scarlet & Violet: Journey Together",
            "Scarlet & Violet: Destined Rivals", "Scarlet & Violet: Black Bolt", "Scarlet & Violet: White Flare"
        ]},
        {"name": "Promos", "sets": [
            "Wizard Black Star Promos", "Nintendo Black Star Promos", "Diamond & Pearl Black Star Promos",
            "HeratGold & SoulSilver Black Star Promos", "Black & White Black Star Promos", "XY Black Star Promos",
            "Sun & Moon Black Star Promos", "Sword & Shield Black Star Promos", "Scarlet & Violet Black Star Promos",
            "Mega Evolution Black Star Promos"
        ]},
        {"name": "Et Cetera", "sets": [
            "Demo Game", "Legendary Collection", "Pokémon Rumble", "Southern Islands", "Call of Legends",
            "Dragon Vault", "Double Crisis", "Generations", "Dragon Majesty", "Detective Pikachu",
            "Hidden Fates", "Champion’s Path", "Shining Fates", "Celebrations", "Celebrations: Classic Collection",
            "Pokémon GO", "McDonald’s 25th anniversary", "McDonald’s 2022", "McDonald’s 2023", "McDonald’s 2025",
            "Trick or Trade 2022", "Trick or Trade 2023", "Trick or Trade 2024", "Crown Zenith"
        ]},
    ]

    # Korean series and sets
    korean_series = [
        {"name": "다이아몬드 & 펄", "sets": [
            "모험의 시작", "불타는 대결", "시공의 격돌", "또 다른 세계", "신비의 일곱", "어둠의 초승달",
            "잠재된 힘", "다채로운 전설", "호수의 기적", "고대의 수호자"
        ]},
        {"name": "블랙 & 화이트", "sets": [
            "블랙 컬렉션", "화이트 컬렉션", "레드 컬렉션", "사이코 드라이브", "헤일 블리자드", "다크 러시",
            "EX 배틀 부스트", "메갈로 캐논", "샤이니 컬렉션", "플라스마 게일", "스파이럴 포스 ", "볼트 너클",
            "프리즈 볼트 ", "콜드 플레어", "드래곤 블라스트", "드래곤 블레이드", "드래곤 컬렉션"
        ]},
        {"name": "XY", "sets": [
            "X 컬렉션", "Y 컬렉션", "와일드 블레이즈", "라이징 피스트", "팬텀 게이트", "가이아 볼케이노",
            "타이달 스톰", "마그마단 vs 아쿠아단 더블 크라이시스", "에메랄드 브레이크", "밴디트 링", "레전드 컬렉션",
            "푸른 충격", "붉은 섬광", "천공의 분노", "초능력의 제왕", "포켓심쿵 컬렉션", "프리미엄 챔피언팩",
            "타오르는 투사", "냉혹한 반역자", "환상・전설 드림 컬렉션", "BASE PACK 20th Anniversary", "THE BEST OF XY"
        ]},
        {"name": "썬 & 문", "sets": [
            "썬 컬렉션", "문 컬렉션", "썬 & 문", "알로라의 햇빛", "알로라의 달빛", "새로운 시련", "어둠을 밝힌 무지개",
            "빛을 삼킨 어둠", "각성의 용사", "초차원의 침략자", "빛나는 전설", "GX 배틀 부스트", "울트라 썬", "울트라 문",
            "울트라 포스", "금단의 빛", "드래곤 스톰", "챔피언 로드", "창공의 카리스마", "페어리 라이즈", "버스트 임팩트",
            "플라스마 스파크", "다크 오더", "GX 울트라 샤이니", "태그 볼트", "나이트 유니슨", "풀 메탈 월", "명탐정 피카츄",
            "더블 블레이즈", "GG 엔드", "스카이 레전드", "미라클 트윈", "리믹스 바우트", "드림 리그", "얼터 제네시스",
            "TAG TEAM GX 태그 올스타즈"
        ]},
        {"name": "소드 & 실드", "sets": [
            "소드", "실드", "VMAX 라이징", "반역 크래시", "폭염 워커", "무한 존", "전설의 고동", "양천의 볼트 태클",
            "샤이니 스타 V", "일격 마스터", "연격 마스터", "쌍벽의 파이터", "백은의 랜스", "칠흑의 가이스트", "이브이 히어로즈",
            "마천 퍼펙트", "창공 스트림", "퓨전 아츠", "25th Anniversary Collection", "VMAX 클라이맥스", "스타 버스",
            "배틀 리전", "타임 게이저", "스페이스 저글러", "다크 판타스마", "Pokémon GO", "로스트 어비스", "백열의 아르카나",
            "패러다임 트리거", "VSTAR 유니버스"
        ]},
        {"name": "스칼렛 & 바이올렛", "sets": [
            "스칼렛 ex", "바이올렛 ex", "트리플렛 비트", "스노 해저드", "클레이 버스트", "포켓몬 카드 151",
            "흑염의 지배자", "레이징 서프", "고대의 포효", "미래의 일섬", "샤이니 트레져 ex", "와일드 포스", "사이버 저지",
            "크림슨 헤이즈", "변환의 가면", "나이트 원더러", "스텔라 미라클", "낙원 드래고나", "초전 브레이커",
            "테라스탈 페스타 ex", "배틀 파트너즈", "열풍의 아레나", "로켓단의 영광", "블랙 볼트", "화이트 플레어"
        ]},
    ]

    # Flatten sets for autocomplete/dropdowns
    sets_flat = []
    for group in english_series + korean_series:
        for s in group.get("sets", []) or []:
            sets_flat.append(s)

    return PokemonCatalog(
        rarities=rarities,
        languages=languages,
        sets={
            "english": {"series": [{"name": g["name"], "sets": g.get("sets", [])} for g in english_series]},
            "korean": {"series": [{"name": g["name"], "sets": g.get("sets", [])} for g in korean_series]},
        },
        sets_flat=sets_flat,
    )


@router.get("/pokemon", response_model=PokemonCatalog)
async def get_pokemon_catalog(mdb=Depends(get_mongo_db)):
    global _CATALOG
    # Return from memory if already loaded
    if _CATALOG is not None:
        return _CATALOG

    # Try Mongo first
    if mongo_enabled() and mdb is not None:
        doc = await mdb["catalog"].find_one({"key": "pokemon"})
        if doc and "data" in doc:
            _CATALOG = PokemonCatalog(**doc["data"])
            return _CATALOG

    # Seed and optionally persist to Mongo
    seeded = _seed_pokemon_catalog()
    if mongo_enabled() and mdb is not None:
        await mdb["catalog"].update_one({"key": "pokemon"}, {"$set": {"data": seeded.model_dump()}}, upsert=True)
    _CATALOG = seeded
    return _CATALOG
