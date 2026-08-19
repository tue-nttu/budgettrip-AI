"""
BudgetTrip AI - Nen tang lap ke hoach du lich thong minh theo ngan sach
Chu de: Da Lat - du lieu thuc te tu Google Sheet cua nguoi dung
"""
import csv
import math
import os
import random
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote
from flask import Flask, render_template, request

from ai_advisor import generate_ai_plan, fallback_advice

app = Flask(__name__)


@app.template_filter("vnd")
def format_vnd(value):
    """Dinh dang so tien kieu Viet Nam: 1234567 -> 1.234.567 d"""
    return f"{int(value):,}".replace(",", ".") + " đ"


@app.template_filter("maps_link")
def maps_link(address):
    """Tra ve link Google Maps tim kiem theo dia chi (khong can API key)."""
    if not address:
        return "#"
    return "https://www.google.com/maps/search/?api=1&query=" + quote(address)


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

TIERS = ["binh_dan", "tieu_chuan", "cao_cap"]
TIER_LABELS = {
    "binh_dan": "Bình dân (Tiết kiệm)",
    "tieu_chuan": "Tiêu chuẩn",
    "cao_cap": "Cao cấp",
}
CATEGORY_LABELS = {
    "hotel": "Khách sạn",
    "food": "Quán ăn",
    "drinks": "Quán nước",
    "attraction": "Tham quan",
    "souvenir": "Đồ lưu niệm",
}
CATEGORY_ICONS = {
    "hotel": "🏨",
    "food": "🍜",
    "drinks": "☕",
    "attraction": "🌲",
    "souvenir": "🎁",
}

# 5 loai hinh luu tru rieng - moi loai 1 file CSV, gop lai thanh 1 pool "hotel" chung
STAY_TYPES = {
    "khach_san": {"label": "Khách sạn", "icon": "🏨", "file": "hotels_khach_san.csv"},
    "villa": {"label": "Villa", "icon": "🏡", "file": "hotels_villa.csv"},
    "homestay": {"label": "Homestay", "icon": "🛖", "file": "hotels_homestay.csv"},
    "nha_nghi": {"label": "Nhà nghỉ", "icon": "🏚️", "file": "hotels_nha_nghi.csv"},
    "can_ho": {"label": "Căn hộ", "icon": "🏢", "file": "hotels_can_ho.csv"},
}

# So "sao cao cap" (khong phai danh gia nguoi dung) toi thieu de duoc uu tien
# theo tung hang gia - hang gia cang cao thi cang uu tien khach san nhieu sao.
TIER_MIN_STARS = {"binh_dan": 0, "tieu_chuan": 3, "cao_cap": 4}

# Tu khoa nhan dien mon "vat" (an vat/trang mieng) - dung lam pool rieng cho "an vat buoi toi"
SNACK_KEYWORDS = [
    "bánh tráng nướng", "cà rem", "kem", "bánh su kem", "xôi xoài",
    "bông lan trứng nướng", "mochi", "cafe", "cà phê", "trà", "chè",
    "sinh tố", "nước ép", "đậu hũ thúi",
]


def is_main_dish(name):
    n = name.lower()
    return not any(kw in n for kw in SNACK_KEYWORDS)


def dish_type(name):
    """Xac dinh 'loai mon' cua 1 mon an vat (vd: banh trang nuong, kem, tra...)."""
    n = name.lower()
    for kw in SNACK_KEYWORDS:
        if kw in n:
            return kw
    return "khac"


def pick_snack(pool, tier, k=3, used_types=None):
    """
    Cho 1 ngay: chon MOT loai mon an vat (vd banh trang nuong) - uu tien loai
    CHUA dung o cac ngay truoc do - roi lay k quan KHAC NHAU cung ban loai
    mon do. Ngay hom sau se uu tien loai mon khac, tranh viec ngay nao cung
    lap lai dung 1 loai (vd banh trang nuong) nhu nhau.

    Luu y: do an vat gia deu thap va it chenh lech nhau, nen o day KHONG loc
    theo tier truoc - de toi da so luong quan cung 1 loai mon co the chon
    (vd du 5 quan banh trang nuong deu nam trong cung 1 hang gia).
    """
    used_types = used_types or set()
    if not pool:
        return []

    for p in pool:
        p["_dish_type"] = dish_type(p["name"])

    by_type = {}
    for p in pool:
        by_type.setdefault(p["_dish_type"], []).append(p)

    types_list = list(by_type.keys())
    random.shuffle(types_list)
    unused_types = [t for t in types_list if t not in used_types]

    # Uu tien loai CHUA dung va co du k quan; neu khong co, chon loai chua
    # dung bat ky; het loai moi thi danh phai chon lai loai da dung.
    full_unused = [t for t in unused_types if len(by_type[t]) >= k]
    if full_unused:
        chosen_type = random.choice(full_unused)
    elif unused_types:
        chosen_type = random.choice(unused_types)
    else:
        full_types = [t for t in types_list if len(by_type[t]) >= k]
        chosen_type = random.choice(full_types) if full_types else random.choice(types_list)

    same_type = by_type[chosen_type]
    if len(same_type) >= k:
        picks = random.sample(same_type, k)
    else:
        picks = list(same_type)
        picked_names = {p["name"] for p in picks}
        remaining_pool = [p for p in pool if p["name"] not in picked_names]
        random.shuffle(remaining_pool)
        # Bu cho trong: uu tien loai CHUA dung o ngay truoc, tranh de loai
        # vua chon lai (chosen_type) hoac loai da dung roi len vao lan nua.
        remaining_pool.sort(key=lambda p: (p["_dish_type"] in used_types))
        picks += remaining_pool[: k - len(picks)]

    picks.sort(key=lambda x: (x.get("rating") or 0), reverse=True)
    return picks

NUM_RE = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+")
TAXI_KM_PER_DAY = 25  # uoc tinh so km di chuyen trung binh moi ngay cho khach du lich


# ---------------------------------------------------------------------------
# Ham chuan hoa du lieu tho tu Google Sheet (gia + danh gia format khong dong nhat)
# ---------------------------------------------------------------------------

def parse_price(text):
    """'70.000 VNĐ' -> 70000 | '20.000 - 100.000 VNĐ' -> 60000 (trung binh) | 'Miễn phí' -> 0"""
    if not text:
        return 0
    t = text.strip()
    if "miễn phí" in t.lower() or "free" in t.lower():
        return 0
    nums = NUM_RE.findall(t)
    if not nums:
        return 0
    vals = [int(n.replace(".", "")) for n in nums]
    return round(sum(vals) / len(vals))


def parse_money(raw):
    """'6.000.000' hoac '6000000' -> 6000000 (int). Chuoi rong -> 0."""
    if raw is None:
        return 0
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else 0


SEASON_INFO = {
    # Da Lat: mua kho (le hoi hoa, Tet, cao diem) ~ thang 12-3
    12: {"name": "Mùa khô – cao điểm lễ hội & Tết",
         "note": "Trời khô ráo, se lạnh về đêm (10-18°C), rất đẹp để chụp ảnh nhưng cũng là mùa cao điểm nên giá phòng, dịch vụ thường tăng — nên đặt phòng trước."},
    1: {"name": "Mùa khô – cao điểm lễ hội & Tết",
        "note": "Trời khô ráo, se lạnh về đêm (10-18°C), rất đẹp để chụp ảnh nhưng cũng là mùa cao điểm nên giá phòng, dịch vụ thường tăng — nên đặt phòng trước."},
    2: {"name": "Mùa khô – cao điểm Tết",
        "note": "Thời điểm Tết Nguyên đán, giá phòng và dịch vụ có thể tăng mạnh, nơi tham quan rất đông — nên đặt phòng và lên lịch trình sớm."},
    3: {"name": "Cuối mùa khô",
        "note": "Vẫn còn nắng ráo, ít mưa, thích hợp cho các hoạt động ngoài trời, giá cả bắt đầu hạ nhiệt so với dịp Tết."},
    4: {"name": "Giao mùa",
        "note": "Thời tiết dễ chịu, nắng nhẹ xen ít mưa giông cuối ngày, giá dịch vụ ở mức trung bình."},
    5: {"name": "Đầu mùa mưa",
        "note": "Bắt đầu có mưa giông vào buổi chiều, nên mang theo áo mưa/ô, buổi sáng vẫn thường nắng đẹp."},
    6: {"name": "Mùa mưa",
        "note": "Mưa nhiều vào chiều tối, nên sắp lịch tham quan ngoài trời vào buổi sáng, mang áo mưa gọn nhẹ."},
    7: {"name": "Mùa mưa",
        "note": "Mưa nhiều vào chiều tối, nên sắp lịch tham quan ngoài trời vào buổi sáng, mang áo mưa gọn nhẹ."},
    8: {"name": "Mùa mưa",
        "note": "Mưa nhiều vào chiều tối, nên sắp lịch tham quan ngoài trời vào buổi sáng, mang áo mưa gọn nhẹ."},
    9: {"name": "Mùa mưa",
        "note": "Mưa nhiều vào chiều tối, nên sắp lịch tham quan ngoài trời vào buổi sáng, mang áo mưa gọn nhẹ."},
    10: {"name": "Cuối mùa mưa",
         "note": "Mưa giảm dần, cây cối xanh tươi sau mưa, giá dịch vụ thường mềm hơn mùa cao điểm."},
    11: {"name": "Giao mùa – bắt đầu se lạnh",
         "note": "Mưa giảm hẳn, trời bắt đầu se lạnh về đêm, là thời điểm khá lý tưởng và giá còn hợp lý trước khi vào cao điểm."},
}


def season_info(month):
    return SEASON_INFO.get(month, {"name": "Không xác định", "note": ""})

def parse_premium_stars(text):
    """
    Dem so 'sao cao cap' (hang sang trong cua co so luu tru, KHONG PHAI diem
    danh gia cua khach). Vd: '⭐️⭐️⭐️⭐️⭐️' -> 5.
    """
    if not text:
        return 0
    return text.count("⭐")


def parse_rating(text):
    """'4,3' | '4.1⭐️' | '⭐ 4.6' | '4.9' | '—' -> float hoac None"""
    if not text:
        return None
    t = text.strip()
    if t in ("", "-", "—", "–"):
        return None
    t2 = re.sub(r"[^\d.,]", "", t)
    t2 = t2.replace(",", ".")
    if not t2:
        return None
    try:
        val = float(t2)
    except ValueError:
        return None
    return val if val > 0 else None


def assign_tiers(items):
    """Chia hang muc binh dan/tieu chuan/cao cap dua tren tam phan vi gia (tercile)."""
    priced = sorted([it for it in items if it["price"] > 0], key=lambda x: x["price"])
    n = len(priced)
    third = max(1, math.ceil(n / 3))
    for idx, it in enumerate(priced):
        if idx < third:
            it["tier"] = "binh_dan"
        elif idx < 2 * third:
            it["tier"] = "tieu_chuan"
        else:
            it["tier"] = "cao_cap"
    for it in items:
        if it["price"] == 0:
            it["tier"] = "binh_dan"
    return items


def load_csv(filename):
    path = os.path.join(DATA_DIR, filename)
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_hotels():
    """Gop 5 loai hinh luu tru tu 5 file CSV rieng thanh 1 pool "hotel" chung."""
    items = []
    next_id = 0
    for stay_key, meta in STAY_TYPES.items():
        for r in load_csv(meta["file"]):
            star_raw = r.get("Sao") or r.get("Sao ") or ""
            items.append({
                "id": next_id,
                "category": "hotel",
                "stay_type": stay_key,
                "stay_type_label": meta["label"],
                "name": r.get("Tên cơ sở", "").strip(),
                "address": r.get("Địa chỉ", "").strip(),
                "price": parse_price(r.get("Giá từ", "")),
                "hours": "",
                "rating": parse_rating(r.get("Đánh giá", "")),
                "premium_stars": parse_premium_stars(star_raw),
                "segment": r.get("Phân khúc", "").strip(),
                "description": r.get("Mô tả", "").strip(),
                "unit": "phòng/đêm",
                "icon": meta["icon"],
            })
            next_id += 1
    return assign_tiers(items)


def build_food():
    items = []
    for idx, r in enumerate(load_csv("food.csv")):
        name = r.get("TÊN QUÁN", "").strip()
        items.append({
            "id": idx,
            "category": "food",
            "name": name,
            "address": r.get("ĐỊA CHỈ", "").strip(),
            "price": parse_price(r.get("GIÁ/NGƯỜI", "")),
            "hours": r.get("GIỜ ĐÓNG/MỞ", "").strip(),
            "rating": parse_rating(r.get("ĐÁNH GIÁ", "")),
            "description": r.get("MÔ TẢ", "").strip(),
            "unit": "người/bữa",
            "icon": CATEGORY_ICONS["food"],
            "is_main": is_main_dish(name),
        })
    return assign_tiers(items)


def build_drinks():
    items = []
    for idx, r in enumerate(load_csv("drinks.csv")):
        items.append({
            "id": idx,
            "category": "drinks",
            "name": r.get("TÊN QUÁN", "").strip(),
            "address": r.get("ĐỊA CHỈ", "").strip(),
            "price": parse_price(r.get("GIÁ/NGƯỜI", "")),
            "hours": r.get("GIỜ ĐÓNG/MỞ", "").strip(),
            "rating": parse_rating(r.get("ĐÁNH GIÁ", "")),
            "description": r.get("MÔ TẢ", "").strip(),
            "unit": "người/ly",
            "icon": CATEGORY_ICONS["drinks"],
        })
    return assign_tiers(items)


def build_attractions():
    items = []
    for idx, r in enumerate(load_csv("attractions.csv")):
        items.append({
            "id": idx,
            "category": "attraction",
            "name": r.get("ĐỊA ĐIỂM", "").strip(),
            "address": r.get("ĐỊA CHỈ", "").strip(),
            "price": parse_price(r.get("GIÁ VÉ THAM QUAN", "")),
            "hours": r.get("GIỜ ĐÓNG/MỞ", "").strip(),
            "rating": parse_rating(r.get("ĐÁNH GIÁ", "")),
            "description": r.get("MÔ TẢ", "").strip(),
            "unit": "người/lượt",
            "icon": CATEGORY_ICONS["attraction"],
        })
    return assign_tiers(items)


def build_souvenirs():
    items = []
    for idx, r in enumerate(load_csv("souvenirs.csv")):
        items.append({
            "id": idx,
            "category": "souvenir",
            "name": r.get("TÊN CỬA HÀNG", "").strip(),
            "address": r.get("ĐỊA CHỈ", "").strip(),
            "price": parse_price(r.get("GIÁ BÁN", "")),
            "hours": r.get("GIỜ ĐÓNG – MỞ", "").strip(),
            "rating": parse_rating(r.get("ĐÁNH GIÁ SAO", "")),
            "description": r.get("MÔ TẢ", "").strip(),
            "unit": "người",
            "icon": CATEGORY_ICONS["souvenir"],
        })
    return assign_tiers(items)


def build_transport():
    items = [{
        "key": "di_bo", "name": "Đi bộ", "icon": "🚶",
        "price_per_day": 0, "unit": "Miễn phí",
        "note": "Phù hợp khi các điểm ở gần trung tâm, đi lại trong bán kính ngắn.",
        "documents": "", "providers": "",
    }]
    car_documents, car_providers = "", ""
    for r in load_csv("transport.csv"):
        raw_name = r.get("LOẠI PHƯƠNG TIỆN", "").strip()
        price_text = r.get("GIÁ THAM KHẢO", "").strip()
        documents = r.get("GIẤY TỜ CẦN CÓ", "").strip()
        providers = r.get("MỘT SỐ ĐƠN VỊ DUNG CẤP UY TÍN", "").strip()
        avg = parse_price(price_text)
        name_lower = raw_name.lower()
        if "xe máy" in name_lower:
            items.append({
                "key": "xe_may", "name": "Xe máy", "icon": "🛵",
                "price_per_day": avg, "unit": f"{price_text} (2 người/xe)",
                "note": price_text, "documents": documents, "providers": providers,
            })
        elif "taxi" in name_lower:
            items.append({
                "key": "taxi", "name": "Taxi", "icon": "🚕",
                "price_per_day": avg * TAXI_KM_PER_DAY,
                "unit": f"{price_text} · ước tính {TAXI_KM_PER_DAY}km/ngày",
                "note": price_text, "documents": documents, "providers": providers,
            })
        elif "ô tô" in name_lower or "oto" in name_lower:
            car_documents, car_providers = documents, providers
    items.append({
        "key": "oto4", "name": "Ô tô 4 chỗ tự lái", "icon": "🚗",
        "price_per_day": (850_000 + 950_000) // 2,
        "unit": "850.000 – 950.000 VNĐ/ngày (tối đa 4 người/xe)",
        "note": "850.000 – 950.000 VNĐ/ngày",
        "documents": car_documents, "providers": car_providers,
    })
    items.append({
        "key": "oto7", "name": "Ô tô 7 chỗ tự lái", "icon": "🚙",
        "price_per_day": (950_000 + 1_250_000) // 2,
        "unit": "950.000 – 1.250.000 VNĐ/ngày (tối đa 7 người/xe)",
        "note": "950.000 – 1.250.000 VNĐ/ngày",
        "documents": car_documents, "providers": car_providers,
    })
    return items


def build_main_food_pool(food_items):
    """Danh sach mon an CHINH (khong tinh do an vat), tinh lai hang muc gia rieng cho nhom nay."""
    main_items = [dict(f) for f in food_items if f.get("is_main")]
    return assign_tiers(main_items)


def build_snack_pool(food_items):
    """Danh sach do an vat / do nuong - dung cho goi y 'an vat buoi toi' kieu cho dem Da Lat."""
    snack_items = [dict(f) for f in food_items if not f.get("is_main")]
    return assign_tiers(snack_items)


def load_all_places():
    food_items = build_food()
    return {
        "hotel": build_hotels(),
        "food": food_items,
        "main_food": build_main_food_pool(food_items),
        "snack": build_snack_pool(food_items),
        "drinks": build_drinks(),
        "attraction": build_attractions(),
        "souvenir": build_souvenirs(),
    }


# ---------------------------------------------------------------------------
# Thuat toan lap lich trinh
# ---------------------------------------------------------------------------

def pick_tier_by_budget(budget_per_person_per_day):
    if budget_per_person_per_day >= 2_000_000:
        return "cao_cap"
    elif budget_per_person_per_day >= 900_000:
        return "tieu_chuan"
    return "binh_dan"


def pick_random(pool, tier, k=3, used=None):
    """
    Chon ngau nhien k lua chon cung hang muc gia (tier), uu tien cac dia diem
    CHUA duoc dung trong chuyen di (de tang da dang giua cac ngay).
    Neu pool khong du dia diem moi thi cho phep lap lai (tranh loi khi du lieu it).
    """
    used = used or set()
    filtered = [p for p in pool if p.get("tier") == tier]
    source = filtered if len(filtered) >= k else pool
    if not source:
        return []
    fresh = [p for p in source if p["name"] not in used]
    chosen_pool = fresh if len(fresh) >= k else source
    k = min(k, len(chosen_pool))
    if k == 0:
        return []
    picks = random.sample(chosen_pool, k)
    # Dia diem xep hang cao nhat (rating) hien len truoc lam "Goi y chinh"
    picks.sort(key=lambda x: (x.get("rating") or 0), reverse=True)
    return picks


def pick_hotels(pool, tier, k=3):
    filtered = [p for p in pool if p.get("tier") == tier]
    source = filtered if len(filtered) >= k else pool
    if not source:
        return []

    min_stars = TIER_MIN_STARS.get(tier, 0)
    starred_enough = [p for p in source if p.get("premium_stars", 0) >= min_stars]
    candidates = starred_enough if len(starred_enough) >= k else source

    random.shuffle(candidates)
    candidates.sort(key=lambda x: x.get("premium_stars", 0), reverse=True)
    picks = candidates[:k]
    picks.sort(key=lambda x: x["price"])
    return picks


def pick_souvenirs(pool, tier, k=3):
    """
    Nhu pick_random, nhung LUON dam bao 'Cho Da Lat' co mat trong goi y
    (dia diem quen thuoc, hau nhu ai cung ghe khi mua qua luu niem).
    """
    market = next((p for p in pool if "chợ đà lạt" in p["name"].lower()), None)
    if not market:
        return pick_random(pool, tier, k)

    remaining_pool = [p for p in pool if p is not market]
    rest = pick_random(remaining_pool, tier, k - 1)
    picks = [market] + rest
    picks.sort(key=lambda x: (x.get("rating") or 0), reverse=True)
    return picks


def avg_price(picks):
    return round(sum(p["price"] for p in picks) / len(picks)) if picks else 0


def transport_cost_for(transport_items, transport_key, people, days):
    item = next((t for t in transport_items if t["key"] == transport_key), transport_items[0])
    if item["key"] == "xe_may":
        qty = math.ceil(people / 2)
    elif item["key"] == "oto4":
        qty = math.ceil(people / 4)
    elif item["key"] == "oto7":
        qty = math.ceil(people / 7)
    else:
        qty = 1
    cost = item["price_per_day"] * qty * days
    return cost, item, qty


# ---------------------------------------------------------------------------
# Ho tro giao tiep voi AI: nen du lieu gui di, chon loc + kiem tra du lieu AI tra ve
# ---------------------------------------------------------------------------

DEFAULT_ALLOCATION_PCT = {"hotel": 30, "food": 30, "drinks": 10, "attraction": 20, "souvenir": 10}
MIN_POOL_SIZE = {"hotel": 3, "main_food": 9, "snack": 4, "drinks": 6, "attraction": 6, "souvenir": 3}


def compact_pool(pool):
    """Rut gon danh sach dia diem xuong con id/name/price/rating de gui cho AI (giam token)."""
    return [
        {"id": p["id"], "name": p["name"], "price": p["price"], "rating": p.get("rating")}
        for p in pool
    ]


def curate_pool_by_ids(pool, ids, tier, min_count=3):
    """
    Loc pool theo danh sach id AI da chon, gan lai tier = hang muc AI da quyet dinh.
    Neu AI chon thieu/khong hop le (id la, khong du so luong), tu dong bo sung
    them dia diem tu pool goc de dam bao thuat toan xep lich trinh luon co du
    lua chon (khong bao gio crash vi thieu du lieu).
    """
    ids = set(ids) if isinstance(ids, list) else set()
    by_id = {p["id"]: p for p in pool}
    chosen = [dict(by_id[i]) for i in ids if i in by_id]
    for p in chosen:
        p["tier"] = tier

    if len(chosen) < min_count:
        chosen_ids = {p["id"] for p in chosen}
        extra = [p for p in pool if p["id"] not in chosen_ids]
        extra.sort(key=lambda x: 0 if x.get("tier") == tier else 1)
        needed = min_count - len(chosen)
        for p in extra[:needed]:
            p2 = dict(p)
            p2["tier"] = tier
            chosen.append(p2)
    return chosen


def normalize_allocation(pct):
    """Dam bao % phan bo AI tra ve la so hop le va cong lai ~100%, neu khong thi dung mac dinh."""
    if not isinstance(pct, dict):
        return dict(DEFAULT_ALLOCATION_PCT)
    try:
        vals = {k: max(float(pct.get(k, 0)), 0) for k in DEFAULT_ALLOCATION_PCT}
    except (TypeError, ValueError):
        return dict(DEFAULT_ALLOCATION_PCT)
    total = sum(vals.values())
    if total <= 0:
        return dict(DEFAULT_ALLOCATION_PCT)
    return {k: round(v / total * 100) for k, v in vals.items()}


def build_day_route_url(day_stop, hotel_address=None):
    """
    Ghep dia chi cua khach san + cac diem den chinh (pick dau tien) trong ngay
    thanh 1 link Google Maps chi duong nhieu chang, giup nguoi dung xem truoc
    khoang cach / vi tri giua cac diem sang - trua - chieu - toi.
    """
    addresses = []
    if hotel_address:
        addresses.append(hotel_address)
    for slot_key in ("morning", "midday", "afternoon", "evening"):
        for block in day_stop.get(slot_key, []):
            picks = block.get("picks") or []
            if picks and picks[0].get("address"):
                addresses.append(picks[0]["address"])

    # Bo cac dia chi trung lap lien tiep (vd hotel trung voi diem dau)
    dedup = []
    for a in addresses:
        if not dedup or dedup[-1] != a:
            dedup.append(a)

    if len(dedup) < 2:
        return None

    origin = quote(dedup[0])
    destination = quote(dedup[-1])
    waypoints = "%7C".join(quote(a) for a in dedup[1:-1])
    url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={destination}&travelmode=driving"
    if waypoints:
        url += f"&waypoints={waypoints}"
    return url


def build_plan(places, transport_items, tier, people, days, nights, rooms, transport_key, contingency_per_person=0):
    hotel_picks = pick_hotels(places["hotel"], tier, 3) if nights > 0 else []
    hotel_price = avg_price(hotel_picks)
    hotel_cost = hotel_price * rooms * nights

    souvenir_picks = pick_souvenirs(places["souvenir"], tier, 3)
    souvenir_cost = avg_price(souvenir_picks) * people

    # "Bo nho" xuyen suot chuyen di de han che lap lai dia diem giua cac ngay
    used_food, used_drinks, used_attraction, used_snack = set(), set(), set(), set()
    used_snack_types = set()

    itinerary = []
    all_attraction_names = []
    food_total = drinks_total = attraction_total = 0

    for d in range(days):
        # --- 3 bua an trong ngay, moi bua 3 mon khac nhau, chon ngau nhien ---
        breakfast = pick_random(places["main_food"], tier, 3, used_food)
        used_food.update(p["name"] for p in breakfast)
        lunch = pick_random(places["main_food"], tier, 3, used_food)
        used_food.update(p["name"] for p in lunch)
        dinner = pick_random(places["main_food"], tier, 3, used_food)
        used_food.update(p["name"] for p in dinner)

        # --- Tham quan CHI xep vao ban ngay (sang/trua), khong xep buoi toi vi da so dong cua ---
        daytime_slots = ["morning", "midday"]
        random.shuffle(daytime_slots)
        num_attraction_slots = random.choice([1, 1, 1, 2])  # da so 1 diem/ngay, thinh thoang 2
        attraction_slots = set(daytime_slots[:num_attraction_slots])

        morning_attraction, midday_attraction = [], []
        if "morning" in attraction_slots:
            morning_attraction = pick_random(places["attraction"], tier, 3, used_attraction)
            used_attraction.update(p["name"] for p in morning_attraction)
        if "midday" in attraction_slots:
            midday_attraction = pick_random(places["attraction"], tier, 3, used_attraction)
            used_attraction.update(p["name"] for p in midday_attraction)

        # --- Ca phe chieu (thoi quen o Da Lat) + quan nuoc & an vat buoi toi (kieu cho dem) ---
        afternoon_drinks = pick_random(places["drinks"], tier, 3, used_drinks)
        used_drinks.update(p["name"] for p in afternoon_drinks)
        evening_drinks = pick_random(places["drinks"], tier, 3, used_drinks)
        used_drinks.update(p["name"] for p in evening_drinks)
        evening_snack = pick_snack(places["snack"], tier, 3, used_snack_types)
        used_snack.update(p["name"] for p in evening_snack)
        used_snack_types.update(p["_dish_type"] for p in evening_snack)

        day_food = avg_price(breakfast) + avg_price(lunch) + avg_price(dinner) + avg_price(evening_snack)
        day_drinks = avg_price(afternoon_drinks) + avg_price(evening_drinks)
        day_attraction = avg_price(morning_attraction) + avg_price(midday_attraction)

        food_total += day_food * people
        drinks_total += day_drinks * people
        attraction_total += day_attraction * people

        all_attraction_names += [p["name"] for p in morning_attraction + midday_attraction]

        # Cau truc linh hoat: moi khung gio la 1 danh sach cac "khoi hoat dong"
        # (khong con co dinh sang=tham quan / trua=quan nuoc / toi=tham quan nua)
        morning_blocks = [{"label": "🍜 Ăn sáng", "picks": breakfast, "category": "food"}]
        if morning_attraction:
            morning_blocks.append({"label": "🌲 Tham quan", "picks": morning_attraction, "category": "attraction"})

        midday_blocks = [{"label": "🍜 Ăn trưa", "picks": lunch, "category": "food"}]
        if midday_attraction:
            midday_blocks.append({"label": "🌲 Tham quan", "picks": midday_attraction, "category": "attraction"})

        afternoon_blocks = [{"label": "☕ Cà phê chiều", "picks": afternoon_drinks, "category": "drinks"}]

        evening_blocks = [
            {"label": "🍜 Ăn tối", "picks": dinner, "category": "food"},
            {"label": "☕ Quán nước tối", "picks": evening_drinks, "category": "drinks"},
            {"label": "🍢 Ăn vặt", "picks": evening_snack, "category": "food"},
        ]
        day_stop = {
            "day": d + 1,
            "morning": morning_blocks,
            "midday": midday_blocks,
            "afternoon": afternoon_blocks,
            "evening": evening_blocks,
        }
        hotel_address = hotel_picks[0]["address"] if hotel_picks else None
        day_stop["maps_url"] = build_day_route_url(day_stop, hotel_address)
        itinerary.append(day_stop)

    transport_cost, transport_item, transport_qty = transport_cost_for(
        transport_items, transport_key, people, days
    )

    contingency_cost = contingency_per_person * people

    total = (
        hotel_cost + food_total + drinks_total + attraction_total
        + souvenir_cost + transport_cost + contingency_cost
    )

    return {
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
        "hotel_picks": hotel_picks,
        "hotel_price": hotel_price,
        "souvenir_picks": souvenir_picks,
        "itinerary": itinerary,
        "attraction_names": all_attraction_names,
        "hotel_cost": hotel_cost,
        "food_cost": food_total,
        "drinks_cost": drinks_total,
        "attraction_cost": attraction_total,
        "souvenir_cost": souvenir_cost,
        "transport_cost": transport_cost,
        "transport_item": transport_item,
        "transport_qty": transport_qty,
        "contingency_per_person": contingency_per_person,
        "contingency_cost": contingency_cost,
        "total": total,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    transport_items = build_transport()
    return render_template("index.html", transport_items=transport_items, stay_types=STAY_TYPES)

@app.route("/result", methods=["POST"])
def result():
    people = max(int(request.form.get("people", 1)), 1)
    budget = max(parse_money(request.form.get("budget", 0)), 0)
    transport_key = "xe_may"  # mac dinh de uoc tinh ban dau; nguoi dung se tu chon lai o trang ket qua
    contingency_per_person = max(parse_money(request.form.get("contingency", 0)), 0)

    # --- Ngay di / ngay ve -> tinh so ngay + xac dinh mua ---
    today = date.today()
    try:
        start_date = datetime.strptime(request.form.get("start_date", ""), "%Y-%m-%d").date()
    except ValueError:
        start_date = today
    try:
        end_date = datetime.strptime(request.form.get("end_date", ""), "%Y-%m-%d").date()
    except ValueError:
        end_date = start_date + timedelta(days=2)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    days = max((end_date - start_date).days + 1, 1)
    season = season_info(start_date.month)

    nights = max(days - 1, 0)
    rooms = math.ceil(people / 2)

    places = load_all_places()
    transport_items = build_transport()

    selected_stay_types = request.form.getlist("stay_types")
    selected_stay_types = [t for t in selected_stay_types if t in STAY_TYPES]
    if not selected_stay_types:
        selected_stay_types = list(STAY_TYPES.keys())
    places["hotel"] = [h for h in places["hotel"] if h.get("stay_type") in selected_stay_types]
    if not places["hotel"]:
        places = load_all_places()

    # Uoc tinh truoc chi phi di chuyen + du phong
    # de biet AI con bao nhieu "ngan sach linh hoat" de phan bo cho khach san/an uong/...
    pre_transport_cost, pre_transport_item, _ = transport_cost_for(
        transport_items, transport_key, people, days
    )
    contingency_cost = contingency_per_person * people
    flexible_budget = max(budget - pre_transport_cost - contingency_cost, 0)

    # Tinh san tong tien cho TAT CA lua chon phuong tien, de nguoi dung tu chon
    # o trang ket qua (thay vi chon truoc o trang bia) - dung cho JS tinh lai tong.
    transport_options = []
    for t in transport_items:
        opt_cost, _, opt_qty = transport_cost_for(transport_items, t["key"], people, days)
        transport_options.append({
            **t,
            "total_cost": opt_cost,
            "qty": opt_qty,
            "is_default": t["key"] == transport_key,
        })

    # --- Goi AI (Google Gemini - mien phi) de AI THAT SU quyet dinh: hang muc,
    #     % phan bo ngan sach, va chon dia diem cu the tu du lieu that ---
    ai_result = generate_ai_plan({
        "days": days,
        "people": people,
        "budget": budget,
        "flexible_budget": flexible_budget,
        "transport_name": pre_transport_item["name"],
        "start_date": start_date.strftime("%d/%m/%Y"),
        "end_date": end_date.strftime("%d/%m/%Y"),
        "season_name": season["name"],
        "season_note": season["note"],
        "pools": {
            "hotel": compact_pool(places["hotel"]),
            "main_food": compact_pool(places["main_food"]),
            "snack": compact_pool(places["snack"]),
            "drinks": compact_pool(places["drinks"]),
            "attraction": compact_pool(places["attraction"]),
            "souvenir": compact_pool(places["souvenir"]),
        },
    })

    ai_driven = ai_result is not None
    allocation_pct = None

    if ai_driven:
        tier = ai_result.get("tier") if ai_result.get("tier") in TIERS else "tieu_chuan"
        allocation_pct = normalize_allocation(ai_result.get("allocation_pct"))

        ai_places = {
            "hotel": curate_pool_by_ids(places["hotel"], ai_result.get("hotel_ids"), tier, MIN_POOL_SIZE["hotel"]),
            "main_food": curate_pool_by_ids(places["main_food"], ai_result.get("food_ids"), tier, MIN_POOL_SIZE["main_food"]),
            "snack": curate_pool_by_ids(places["snack"], ai_result.get("snack_ids"), tier, MIN_POOL_SIZE["snack"]),
            "drinks": curate_pool_by_ids(places["drinks"], ai_result.get("drink_ids"), tier, MIN_POOL_SIZE["drinks"]),
            "attraction": curate_pool_by_ids(places["attraction"], ai_result.get("attraction_ids"), tier, MIN_POOL_SIZE["attraction"]),
            "souvenir": curate_pool_by_ids(places["souvenir"], ai_result.get("souvenir_ids"), tier, MIN_POOL_SIZE["souvenir"]),
        }

        plan = build_plan(
            ai_places, transport_items, tier, people, days, nights, rooms,
            transport_key, contingency_per_person
        )
        over_budget = plan["total"] > budget
        ai_advice = ai_result.get("advice") or fallback_advice({
            "tier_label": plan["tier_label"], "attraction_names": plan["attraction_names"],
            "season_name": season["name"], "season_note": season["note"],
        })
    else:
        # --- AI khong kha dung (chua co key / het quota / loi mang) ---
        # -> dung lai thuat toan dua tren luat (rule-based) nhu ban goc, app KHONG bao gio crash.
        budget_per_person_per_day = budget / (people * days) if people * days else 0
        start_tier = pick_tier_by_budget(budget_per_person_per_day)
        start_idx = TIERS.index(start_tier)

        plan = None
        for tier in TIERS[start_idx::-1]:
            candidate = build_plan(
                places, transport_items, tier, people, days, nights, rooms,
                transport_key, contingency_per_person
            )
            if candidate["total"] <= budget:
                plan = candidate
                break

        over_budget = plan is None
        if plan is None:
            plan = build_plan(
                places, transport_items, "binh_dan", people, days, nights, rooms,
                transport_key, contingency_per_person
            )
        ai_advice = fallback_advice({
            "tier_label": plan["tier_label"], "attraction_names": plan["attraction_names"],
            "season_name": season["name"], "season_note": season["note"],
        })

    remaining = budget - plan["total"]

    return render_template(
        "result.html",
        people=people, days=days, nights=nights, rooms=rooms, budget=budget,
        start_date=start_date, end_date=end_date, season=season,
        plan=plan, remaining=remaining, over_budget=over_budget, ai_advice=ai_advice,
        ai_driven=ai_driven, allocation_pct=allocation_pct, transport_options=transport_options,
    )


@app.route("/browse", methods=["GET"])
def browse():
    places = load_all_places()
    places.pop("main_food", None)  # danh sach noi bo dung cho thuat toan, khong hien thi rieng
    places.pop("snack", None)      # da gop vao "food" o tren, khong can hien thi rieng
    return render_template(
        "browse.html",
        places=places,
        category_labels=CATEGORY_LABELS,
        tier_labels=TIER_LABELS,
    )


if __name__ == "__main__":
    app.run(debug=False)
