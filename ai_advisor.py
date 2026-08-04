"""
ai_advisor.py - Tich hop AI (Google Gemini - MIEN PHI) de AI THAT SU tham gia
vao viec goi y chi tieu cho chuyen di, thay vi chi sinh loi khuyen suong.

AI dam nhiem 3 viec:
1) Quyet dinh hang muc chi tieu (binh_dan / tieu_chuan / cao_cap) phu hop
   voi ngan sach, thay vi cong thuc nguong co dinh.
2) Phan bo % ngan sach cho tung khoan (khach san / an uong / quan nuoc /
   tham quan / qua luu niem).
3) Chon ra danh sach dia diem CU THE (tu du lieu that trong CSV, thong qua
   "id") phu hop voi ngan sach va hang muc - thay vi random/sap gia don gian.

Ban nay dung SDK CHINH THUC `google-genai` (khong tu goi REST bang urllib)
vi key dang moi "AQ." cua Google chi hoat dong dung qua SDK chinh thuc.

Neu khong co GEMINI_API_KEY, chua cai thu vien, loi mang, hoac AI tra ve
JSON khong hop le -> generate_ai_plan() tra ve None. Luc do app.py se TU
DONG dung lai thuat toan dua tren luat (rule-based) nhu ban cu, dam bao
app KHONG BAO GIO bi crash hay dung lai vi AI loi/het quota (quan trong
khi demo dong nguoi cung luc).

Cach lay key MIEN PHI: https://aistudio.google.com/apikey (khong can the),
roi set bien moi truong GEMINI_API_KEY.
"""
import json
import os

MODEL = "gemini-2.0-flash"
API_KEY = os.environ.get("GEMINI_API_KEY")

try:
    from google import genai
    from google.genai import types as genai_types
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "tier": {
            "type": "string",
            "enum": ["binh_dan", "tieu_chuan", "cao_cap"],
        },
        "allocation_pct": {
            "type": "object",
            "properties": {
                "hotel": {"type": "integer"},
                "food": {"type": "integer"},
                "drinks": {"type": "integer"},
                "attraction": {"type": "integer"},
                "souvenir": {"type": "integer"},
            },
            "required": ["hotel", "food", "drinks", "attraction", "souvenir"],
        },
        "hotel_ids": {"type": "array", "items": {"type": "integer"}},
        "food_ids": {"type": "array", "items": {"type": "integer"}},
        "snack_ids": {"type": "array", "items": {"type": "integer"}},
        "drink_ids": {"type": "array", "items": {"type": "integer"}},
        "attraction_ids": {"type": "array", "items": {"type": "integer"}},
        "souvenir_ids": {"type": "array", "items": {"type": "integer"}},
        "advice": {"type": "string"},
    },
    "required": [
        "tier", "allocation_pct", "hotel_ids", "food_ids", "snack_ids",
        "drink_ids", "attraction_ids", "souvenir_ids", "advice",
    ],
}


def _fallback_advice(ctx):
    """Loi khuyen mac dinh khi khong goi duoc AI - dam bao app luon hoat dong."""
    tier_label = ctx.get("tier_label", "").lower()
    tips = []
    if tier_label:
        tips.append(f"Lịch trình này được xếp ở hạng mức {tier_label}, phù hợp với ngân sách bạn đưa ra.")
    season_name = ctx.get("season_name")
    season_note = ctx.get("season_note")
    if season_name:
        tips.append(f"Chuyến đi rơi vào {season_name.lower()}: {season_note}")
    else:
        tips.append("Đà Lạt buổi sáng và tối khá lạnh (15-18°C), nên mang theo áo khoác nhẹ.")
    if ctx.get("attraction_names"):
        tips.append("Nên đi tham quan sớm để tránh đông và có ánh sáng đẹp để chụp ảnh.")
    tips.append("Buổi tối ghé khu chợ đêm để ăn vặt, uống nước và mua quà lưu niệm sẽ tiết kiệm hơn nhà hàng.")
    return " ".join(tips)


def fallback_advice(ctx):
    """Ham public de app.py dung lai khi AI khong kha dung."""
    return _fallback_advice(ctx)


def _build_prompt(ctx):
    pools_json = json.dumps(ctx["pools"], ensure_ascii=False, separators=(",", ":"))
    return f"""Bạn là chuyên gia lập kế hoạch chi tiêu du lịch cho ứng dụng BudgetTrip AI (du lịch Đà Lạt).
Nhiệm vụ của bạn KHÔNG PHẢI chỉ đưa lời khuyên, mà là THẬT SỰ QUYẾT ĐỊNH cách chi tiêu:

1. Chọn "tier" (hạng mức) phù hợp nhất với ngân sách: "binh_dan" (tiết kiệm), "tieu_chuan", hoặc "cao_cap".
2. Phân bổ % ngân sách linh hoạt (allocation_pct) cho 5 khoản: hotel, food, drinks, attraction, souvenir
   (5 số phải cộng lại xấp xỉ 100).
3. Từ các danh sách địa điểm THẬT bên dưới (mỗi item có "id","name","price" là giá tham khảo VNĐ),
   hãy CHỌN RA những id phù hợp nhất với ngân sách và hạng mức đã chọn ở bước 1 (ưu tiên rating cao,
   giá hợp lý so với phần % đã phân bổ). Số lượng id cần chọn cho mỗi danh mục:
   - hotel_ids: 3-5 id
   - food_ids: 10-15 id (đây là món ăn chính, dùng cho 3 bữa/ngày)
   - snack_ids: 4-6 id (đồ ăn vặt buổi tối)
   - drink_ids: 6-8 id (quán nước/cà phê)
   - attraction_ids: 6-8 id (điểm tham quan)
   - souvenir_ids: 3-4 id (quà lưu niệm)
4. Viết "advice": 1 đoạn ngắn (3-4 câu, tiếng Việt có dấu, giọng thân thiện) tư vấn thực tế cho chuyến đi.
   BẮT BUỘC phải nhắc đến mùa/thời tiết của chuyến đi (dựa vào thông tin mùa bên dưới) và những lưu ý
   phù hợp với mùa đó (mặc gì, mang theo gì, giá cả mùa này thế nào, có nên đặt trước không...), ngoài ra
   có thể thêm mẹo di chuyển hoặc mẹo tiết kiệm. Không markdown, không mở đầu kiểu "Dưới đây là...".

CHỈ được chọn id có thật trong danh sách cung cấp bên dưới, không được tự bịa id.

Thông tin chuyến đi:
- Ngày đi: {ctx.get('start_date', '?')} | Ngày về: {ctx.get('end_date', '?')} | Số ngày: {ctx['days']} | Số người: {ctx['people']}
- Mùa của chuyến đi: {ctx.get('season_name', '?')} — {ctx.get('season_note', '')}
- Tổng ngân sách: {ctx['budget']:,} đ
- Ngân sách linh hoạt (đã trừ chi phí di chuyển + dự phòng cố định): {ctx['flexible_budget']:,} đ cho {ctx['days']} ngày, {ctx['people']} người
- Phương tiện đã chọn: {ctx['transport_name']}

Danh sách địa điểm thật (JSON, mỗi danh mục là 1 mảng các item {{"id","name","price","rating"}}):
{pools_json}

Trả lời CHỈ đúng 1 object JSON theo schema đã cung cấp, không thêm chữ nào khác ngoài JSON.
"""


def generate_ai_plan(ctx):
    """
    ctx: dict gom:
      - days, people, budget, flexible_budget, transport_name
      - pools: dict {{hotel, main_food, snack, drinks, attraction, souvenir}}
        moi gia tri la list [{{"id","name","price","rating"}}, ...]
    Tra ve: dict theo PLAN_SCHEMA neu thanh cong, None neu that bai (de app.py
    tu chuyen sang thuat toan rule-based).
    """
    if not API_KEY or not _SDK_AVAILABLE:
        return None

    prompt = _build_prompt(ctx)

    try:
        client = genai.Client(api_key=API_KEY)
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=2000,
                temperature=0.4,
                response_mime_type="application/json",
                response_schema=PLAN_SCHEMA,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            return None
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        # Loi mang / het quota / sai key / JSON khong hop le -> de app.py fallback
        return None
