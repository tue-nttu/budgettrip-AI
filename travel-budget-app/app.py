"""
BudgetTrip AI - Nen tang lap ke hoach du lich thong minh theo ngan sach
Chu de: Da Lat - du lieu thuc te tu Google Sheet cua nguoi dung
"""
import csv
import math
import os
import random
import re
from flask import Flask, render_template, request

from ai_advisor import generate_ai_advice

app = Flask(__name__)


@app.template_filter("vnd")
def format_vnd(value):
    """Dinh dang so tien kieu Viet Nam: 1234567 -> 1.234.567 d"""
    return f"{int(value):,}".replace(",", ".") + " đ"


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

# Tu khoa nhan dien mon "vat" (an vat/trang mieng) - dung lam pool rieng cho "an vat buoi toi"
SNACK_KEYWORDS = [
    "bánh tráng nướng", "cà rem", "kem", "bánh su kem", "xôi xoài",
    "bông lan trứng nướng", "mochi", "cafe", "cà phê", "trà", "chè",
    "sinh tố", "nước ép", "đậu hũ thúi",
]


def is_main_dish(name):
    n = name.lower()
    return not any(kw in n for kw in SNACK_KEYWORDS)


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
    items = []
    for r in load_csv("hotels.csv"):
        items.append({
            "category": "hotel",
            "name": r.get("Khách sạn/Homestay", "").strip(),
            "address": r.get("Địa chỉ", "").strip(),
            "price": parse_price(r.get("Giá từ", "")),
            "hours": "",
            "rating": parse_rating(r.get("Đánh giá", "")),
            "description": r.get("Mô tả", "").strip(),
            "unit": "phòng/đêm",
            "icon": CATEGORY_ICONS["hotel"],
        })
    return assign_tiers(items)


def build_food():
    items = []
    for r in load_csv("food.csv"):
        name = r.get("TÊN QUÁN", "").strip()
        items.append({
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
    for r in load_csv("drinks.csv"):
        items.append({
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
    for r in load_csv("attractions.csv"):
        items.append({
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
    for r in load_csv("souvenirs.csv"):
        items.append({
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
        "key": "di_bo",
        "name": "Đi bộ",
        "icon": "🚶",
        "price_per_day": 0,
        "unit": "Miễn phí",
        "note": "Phù hợp khi các điểm ở gần trung tâm, đi lại trong bán kính ngắn.",
    }]
    for r in load_csv("transport.csv"):
        raw_name = r.get("LOẠI PHƯƠNG TIỆN", "").strip()
        price_text = r.get("GIÁ THAM KHẢO", "").strip()
        avg = parse_price(price_text)
        name_lower = raw_name.lower()
        if "xe máy" in name_lower:
            items.append({
                "key": "xe_may", "name": "Xe máy", "icon": "🛵",
                "price_per_day": avg, "unit": f"{price_text} (2 người/xe)",
                "note": price_text,
            })
        elif "taxi" in name_lower:
            items.append({
                "key": "taxi", "name": "Taxi", "icon": "🚕",
                "price_per_day": avg * TAXI_KM_PER_DAY,
                "unit": f"{price_text} · ước tính {TAXI_KM_PER_DAY}km/ngày",
                "note": price_text,
            })
        elif "ô tô" in name_lower or "oto" in name_lower:
            items.append({
                "key": "oto", "name": "Ô tô tự lái", "icon": "🚗",
                "price_per_day": avg, "unit": f"{price_text} (tối đa 7 người/xe)",
                "note": price_text,
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
    """Nhu pick_random, nhung sap xep 3 lua chon theo GIA TANG DAN (thap -> cao)."""
    picks = pick_random(pool, tier, k)
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
    elif item["key"] == "oto":
        qty = math.ceil(people / 7)
    else:
        qty = 1
    cost = item["price_per_day"] * qty * days
    return cost, item, qty


def build_plan(places, transport_items, tier, people, days, nights, rooms, transport_key, contingency_per_person=0):
    hotel_picks = pick_hotels(places["hotel"], tier, 3) if nights > 0 else []
    hotel_price = avg_price(hotel_picks)
    hotel_cost = hotel_price * rooms * nights

    souvenir_picks = pick_souvenirs(places["souvenir"], tier, 3)
    souvenir_cost = avg_price(souvenir_picks) * people

    # "Bo nho" xuyen suot chuyen di de han che lap lai dia diem giua cac ngay
    used_food, used_drinks, used_attraction, used_snack = set(), set(), set(), set()

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
        evening_snack = pick_random(places["snack"], tier, 3, used_snack)
        used_snack.update(p["name"] for p in evening_snack)

        day_food = avg_price(breakfast) + avg_price(lunch) + avg_price(dinner) + avg_price(evening_snack)
        day_drinks = avg_price(afternoon_drinks) + avg_price(evening_drinks)
        day_attraction = avg_price(morning_attraction) + avg_price(midday_attraction)

        food_total += day_food * people
        drinks_total += day_drinks * people
        attraction_total += day_attraction * people

        all_attraction_names += [p["name"] for p in morning_attraction + midday_attraction]

        # Cau truc linh hoat: moi khung gio la 1 danh sach cac "khoi hoat dong"
        # (khong con co dinh sang=tham quan / trua=quan nuoc / toi=tham quan nua)
        morning_blocks = [{"label": "🍜 Ăn sáng", "picks": breakfast}]
        if morning_attraction:
            morning_blocks.append({"label": "🌲 Tham quan", "picks": morning_attraction})

        midday_blocks = [{"label": "🍜 Ăn trưa", "picks": lunch}]
        if midday_attraction:
            midday_blocks.append({"label": "🌲 Tham quan", "picks": midday_attraction})

        afternoon_blocks = [{"label": "☕ Cà phê chiều", "picks": afternoon_drinks}]

        evening_blocks = [
            {"label": "🍜 Ăn tối", "picks": dinner},
            {"label": "☕ Quán nước tối", "picks": evening_drinks},
            {"label": "🍢 Ăn vặt / đồ nướng", "picks": evening_snack},
        ]

        itinerary.append({
            "day": d + 1,
            "morning": morning_blocks,
            "midday": midday_blocks,
            "afternoon": afternoon_blocks,
            "evening": evening_blocks,
        })

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
    return render_template("index.html", transport_items=transport_items)


@app.route("/result", methods=["POST"])
def result():
    people = max(int(request.form.get("people", 1)), 1)
    days = max(int(request.form.get("days", 1)), 1)
    budget = max(int(request.form.get("budget", 0)), 0)
    transport_key = request.form.get("transport", "di_bo")
    contingency_per_person = max(int(request.form.get("contingency", 0) or 0), 0)

    nights = max(days - 1, 0)
    rooms = math.ceil(people / 2)

    places = load_all_places()
    transport_items = build_transport()

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

    remaining = budget - plan["total"]

    # --- Goi AI (Claude) de sinh loi khuyen ca nhan hoa cho chuyen di ---
    ai_advice = generate_ai_advice({
        "days": days,
        "people": people,
        "tier_label": plan["tier_label"],
        "attraction_names": plan["attraction_names"],
        "transport_name": plan["transport_item"]["name"],
        "total_cost": plan["total"],
        "budget": budget,
    })

    return render_template(
        "result.html",
        people=people, days=days, nights=nights, rooms=rooms, budget=budget,
        plan=plan, remaining=remaining, over_budget=over_budget, ai_advice=ai_advice,
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