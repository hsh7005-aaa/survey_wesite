import os
import json
import uuid
from datetime import datetime
from flask import (
    Flask, render_template, request,
    redirect, url_for, session, jsonify, abort
)
from upstash_redis import Redis

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static"
)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# ── Redis 연결 ──────────────────────────────────────────────
redis = Redis(
    url=os.environ.get("UPSTASH_REDIS_REST_URL", ""),
    token=os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin1234")

# ══════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════

def get_questions():
    """Redis에서 문항 목록 조회"""
    data = redis.get("survey:questions")
    if data:
        return json.loads(data)
    # 기본 샘플 문항
    default = [
        {
            "id": "q1",
            "text": "현재 업무 만족도는 어느 정도입니까?",
            "type": "scale",       # scale / choice / text
            "options": ["1", "2", "3", "4", "5"],
            "required": True
        },
        {
            "id": "q2",
            "text": "귀하의 소속 부서를 선택하세요.",
            "type": "choice",
            "options": ["설계1팀", "설계2팀", "구조팀", "기계팀", "경영지원"],
            "required": True
        },
        {
            "id": "q3",
            "text": "개선이 필요한 사항을 자유롭게 작성해주세요.",
            "type": "text",
            "options": [],
            "required": False
        }
    ]
    redis.set("survey:questions", json.dumps(default, ensure_ascii=False))
    return default


def save_questions(questions):
    redis.set("survey:questions", json.dumps(questions, ensure_ascii=False))


def get_responses():
    """전체 응답 목록 조회"""
    data = redis.get("survey:responses")
    return json.loads(data) if data else []


def save_response(response_data):
    responses = get_responses()
    responses.append(response_data)
    redis.set("survey:responses", json.dumps(responses, ensure_ascii=False))


def get_survey_meta():
    data = redis.get("survey:meta")
    if data:
        return json.loads(data)
    default = {
        "title": "AA아키그룹 조직문화 설문조사",
        "description": "본 설문은 익명으로 처리되며, 결과는 조직문화 개선에 활용됩니다.",
        "active": True
    }
    redis.set("survey:meta", json.dumps(default, ensure_ascii=False))
    return default


def save_survey_meta(meta):
    redis.set("survey:meta", json.dumps(meta, ensure_ascii=False))


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════
# 설문 라우트
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    meta = get_survey_meta()
    if not meta.get("active"):
        return render_template("survey.html", closed=True, meta=meta)
    questions = get_questions()
    return render_template("survey.html", questions=questions, meta=meta, closed=False)


@app.route("/submit", methods=["POST"])
def submit():
    meta = get_survey_meta()
    if not meta.get("active"):
        abort(403)

    questions = get_questions()
    answers = {}
    errors = []

    for q in questions:
        val = request.form.get(q["id"], "").strip()
        if q["required"] and not val:
            errors.append(f"'{q['text']}' 항목은 필수입니다.")
        answers[q["id"]] = val

    if errors:
        return render_template(
            "survey.html",
            questions=questions,
            meta=meta,
            errors=errors,
            prev_answers=answers,
            closed=False
        )

    response_data = {
        "id": str(uuid.uuid4()),
        "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "answers": answers
    }
    save_response(response_data)
    return render_template("survey.html", submitted=True, meta=meta)


# ══════════════════════════════════════════════════════════════
# 결과/차트 라우트
# ══════════════════════════════════════════════════════════════

@app.route("/results")
def results():
    questions = get_questions()
    responses = get_responses()
    meta = get_survey_meta()
    total = len(responses)

    stats = []
    for q in questions:
        qstat = {
            "id": q["id"],
            "text": q["text"],
            "type": q["type"],
            "total": total
        }

        if q["type"] in ("scale", "choice"):
            counts = {}
            for opt in q["options"]:
                counts[opt] = 0
            for r in responses:
                val = r["answers"].get(q["id"], "")
                if val in counts:
                    counts[val] += 1
            qstat["counts"] = counts
            qstat["labels"] = list(counts.keys())
            qstat["values"] = list(counts.values())

            if q["type"] == "scale" and total > 0:
                numeric_vals = []
                for r in responses:
                    try:
                        numeric_vals.append(int(r["answers"].get(q["id"], 0)))
                    except ValueError:
                        pass
                qstat["average"] = round(sum(numeric_vals) / len(numeric_vals), 2) if numeric_vals else 0

        elif q["type"] == "text":
            texts = [
                r["answers"].get(q["id"], "")
                for r in responses
                if r["answers"].get(q["id"], "").strip()
            ]
            qstat["texts"] = texts

        stats.append(qstat)

    return render_template("results.html", stats=stats, meta=meta, total=total)


@app.route("/api/results")
def api_results():
    """Chart.js용 JSON API"""
    questions = get_questions()
    responses = get_responses()
    payload = []
    for q in questions:
        if q["type"] in ("scale", "choice"):
            counts = {opt: 0 for opt in q["options"]}
            for r in responses:
                val = r["answers"].get(q["id"], "")
                if val in counts:
                    counts[val] += 1
            payload.append({
                "id": q["id"],
                "text": q["text"],
                "type": q["type"],
                "labels": list(counts.keys()),
                "values": list(counts.values())
            })
    return jsonify(payload)


# ══════════════════════════════════════════════════════════════
# 어드민 라우트
# ══════════════════════════════════════════════════════════════

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        error = "비밀번호가 올바르지 않습니다."
    return render_template("admin.html", page="login", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    questions = get_questions()
    meta = get_survey_meta()
    responses = get_responses()
    return render_template(
        "admin.html",
        page="dashboard",
        questions=questions,
        meta=meta,
        total=len(responses)
    )


@app.route("/admin/meta", methods=["POST"])
@admin_required
def admin_update_meta():
    meta = get_survey_meta()
    meta["title"] = request.form.get("title", meta["title"])
    meta["description"] = request.form.get("description", meta["description"])
    meta["active"] = request.form.get("active") == "on"
    save_survey_meta(meta)
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/questions/add", methods=["POST"])
@admin_required
def admin_add_question():
    questions = get_questions()
    q_type = request.form.get("type", "choice")
    options_raw = request.form.get("options", "")
    options = [o.strip() for o in options_raw.split("\n") if o.strip()]

    new_q = {
        "id": f"q{uuid.uuid4().hex[:6]}",
        "text": request.form.get("text", "").strip(),
        "type": q_type,
        "options": options if q_type != "text" else [],
        "required": request.form.get("required") == "on"
    }
    if new_q["text"]:
        questions.append(new_q)
        save_questions(questions)
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/questions/delete/<qid>", methods=["POST"])
@admin_required
def admin_delete_question(qid):
    questions = get_questions()
    questions = [q for q in questions if q["id"] != qid]
    save_questions(questions)
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/questions/reorder", methods=["POST"])
@admin_required
def admin_reorder_questions():
    order = request.json.get("order", [])
    questions = get_questions()
    q_map = {q["id"]: q for q in questions}
    reordered = [q_map[qid] for qid in order if qid in q_map]
    save_questions(reordered)
    return jsonify({"ok": True})


@app.route("/admin/responses/clear", methods=["POST"])
@admin_required
def admin_clear_responses():
    redis.delete("survey:responses")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/responses/export")
@admin_required
def admin_export():
    """CSV 다운로드"""
    import csv
    import io
    from flask import Response

    questions = get_questions()
    responses = get_responses()

    output = io.StringIO()
    writer = csv.writer(output)

    header = ["응답ID", "제출시각"] + [q["text"] for q in questions]
    writer.writerow(header)

    for r in responses:
        row = [r["id"], r["submitted_at"]]
        for q in questions:
            row.append(r["answers"].get(q["id"], ""))
        writer.writerow(row)

    output.seek(0)
    return Response(
        "\ufeff" + output.getvalue(),   # BOM for Excel
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=survey_responses.csv"}
    )


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(debug=True)