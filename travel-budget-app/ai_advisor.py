"""
ai_advisor.py - Tich hop AI (Google Gemini - MIEN PHI) de sinh loi khuyen
ca nhan hoa cho tung lich trinh du lich.

Ban nay goi thang REST API cua Google Gemini bang thu vien co san `urllib`
(KHONG can pip install goi gi them) - tranh loi build goi tren mot so may.

Vi sao dung Gemini thay vi Claude/OpenAI:
- Google AI Studio cap API key MIEN PHI, khong can nhap the/thanh toan,
  co han muc goi mien phi (free tier) du dung cho demo/do an.

Neu chua co GEMINI_API_KEY (vi du khi demo local / cham bai chua kip cau hinh),
ham se tu dong fallback ve loi khuyen dua tren luat (rule-based) de app
KHONG BAO GIO bi crash vi thieu key hay mat mang.

Cach lay key MIEN PHI va bat AI that:
1) Vao https://aistudio.google.com/apikey , dang nhap bang tai khoan Google
2) Bam "Create API key" -> copy key (dang AIzaSy...)
3) Set bien moi truong GEMINI_API_KEY:
   - Windows PowerShell:  $env:GEMINI_API_KEY="AIzaSy-xxxxxxxx"
   - macOS/Linux:         export GEMINI_API_KEY="AIzaSy-xxxxxxxx"
   - Tren Render:         Settings > Environment > Add Environment Variable
"""
import json
import os
import urllib.request
import urllib.error

MODEL = "gemini-2.0-flash"
API_KEY = os.environ.get("GEMINI_API_KEY")
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def _fallback_advice(ctx):
    """Loi khuyen mac dinh khi khong goi duoc AI - dam bao app luon hoat dong."""
    tier = ctx["tier_label"].lower()
    tips = [
        f"Lịch trình này được xếp ở hạng mức {tier}, phù hợp với ngân sách bạn đưa ra.",
        "Đà Lạt buổi sáng và tối khá lạnh (15-18°C), nên mang theo áo khoác nhẹ.",
    ]
    if ctx.get("attraction_names"):
        tips.append("Nên đi tham quan sớm để tránh đông và có ánh sáng đẹp để chụp ảnh.")
    tips.append("Buổi tối ghé khu chợ đêm để ăn vặt, uống nước và mua quà lưu niệm sẽ tiết kiệm hơn nhà hàng.")
    return " ".join(tips)


def generate_ai_advice(ctx):
    """
    ctx: dict tom tat chuyen di, gom:
      - days, people, tier_label
      - attraction_names: list ten diem tham quan trong chuyen di
      - transport_name: ten phuong tien di chuyen
      - total_cost: tong chi phi uoc tinh (int)
      - budget: ngan sach nguoi dung nhap (int)
    Tra ve: 1 doan text tieng Viet (fallback neu khong co API key hoac loi mang).
    """
    if not API_KEY:
        return _fallback_advice(ctx)

    prompt = f"""Bạn là hướng dẫn viên du lịch AI cho ứng dụng BudgetTrip AI (lập kế hoạch du lịch Đà Lạt theo ngân sách).
Dựa trên thông tin chuyến đi dưới đây, hãy viết 1 đoạn ngắn (3-4 câu, giọng thân thiện, gần gũi, tiếng Việt có dấu)
đưa ra lời khuyên THỰC TẾ và HỮU ÍCH cho chuyến đi (ví dụ: thời tiết nên mặc gì, cách di chuyển giữa các điểm cho hợp lý,
mẹo tiết kiệm chi phí, thứ tự nên đi buổi nào). Không liệt kê lại các địa điểm đã có, chỉ đưa lời khuyên tổng quan mới.
Chỉ trả lời đúng đoạn văn, không markdown, không tiêu đề, không mở đầu kiểu "Dưới đây là...".

Thông tin chuyến đi:
- Số ngày: {ctx['days']}
- Số người: {ctx['people']}
- Hạng mức chi tiêu: {ctx['tier_label']}
- Các điểm tham quan trong lịch trình: {', '.join(ctx['attraction_names']) or 'không có điểm tham quan cố định'}
- Phương tiện di chuyển: {ctx['transport_name']}
- Ngân sách: {ctx['budget']:,} đ | Chi phí ước tính: {ctx['total_cost']:,} đ
"""

    payload = json.dumps({
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "maxOutputTokens": 300,
            "temperature": 0.7,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{API_URL}?key={API_KEY}",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        candidates = data.get("candidates", [])
        text = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
        return text or _fallback_advice(ctx)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, Exception):
        # Loi mang / het quota / sai key -> khong lam sap app, chi fallback
        return _fallback_advice(ctx)