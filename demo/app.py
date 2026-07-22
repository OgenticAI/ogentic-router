"""ogentic-router live demo — Streamlit UI (OGE-1578).

Type or pick a prompt; see how the router classifies its sensitivity and decides
which backend it's allowed to go to — **before any content leaves the machine**.
No LLM is called: this shows the routing *decision*, which is the whole point.

Run locally:
    pip install -e ".[shield]" streamlit
    streamlit run demo/app.py

Deploy: see demo/README.md (Railway).
"""

from __future__ import annotations

import streamlit as st
from demo.router_demo import BACKENDS, SAMPLES, build_router, route_prompt

st.set_page_config(page_title="ogentic-router demo", page_icon="🧭", layout="centered")


@st.cache_resource(show_spinner="Loading the Shield classifier…")
def _router():  # type: ignore[no-untyped-def]
    return build_router()


st.title("🧭 ogentic-router")
st.markdown(
    "**Privacy-aware LLM routing.** The routing decision happens **on the device, "
    "before the content leaves.** Shield classifies the prompt → a policy picks a "
    "backend → only cleared content would cross the network. This demo shows the "
    "**decision** — no model is called, nothing is sent."
)

with st.sidebar:
    st.header("Backends")
    for _bid, meta in BACKENDS.items():
        st.markdown(f"{meta['icon']} **{meta['kind']}** — {meta['location']}")
    st.caption(
        "Regulated content (privilege / PHI / MNPI) stays on-device; only lower-"
        "sensitivity content is eligible for cloud, redacted first where the policy "
        "says so."
    )
    st.divider()
    st.caption("[GitHub](https://github.com/OgenticAI/ogentic-router) · Apache-2.0")

sample_labels = ["— pick a sample —"] + [label for label, _ in SAMPLES]
choice = st.selectbox("Try a sample prompt", sample_labels)
default_text = ""
if choice != sample_labels[0]:
    default_text = dict((label, prompt) for label, prompt in SAMPLES)[choice]

prompt = st.text_area("Prompt", value=default_text, height=120, placeholder="Type a prompt to route…")

if st.button("Route it", type="primary", disabled=not prompt.strip()):
    result = route_prompt(_router(), prompt.strip())

    # Headline: where it went and whether it stayed local.
    if result.stayed_local:
        st.success(
            f"{result.backend_icon} Stays **on-device** → **{result.backend_kind}** "
            f"(`{result.backend_id}`). Nothing leaves the machine."
        )
    else:
        redacted = " (redacted first)" if result.transform == "shield_redact" else ""
        st.info(
            f"{result.backend_icon} Eligible for **cloud** → **{result.backend_kind}** "
            f"(`{result.backend_id}`){redacted}."
        )

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Classification")
        st.metric("Sensitivity score", f"{result.score}/100")
        st.write("**Groups:**", ", ".join(result.groups) if result.groups else "none")
        st.write("**Top category:**", result.top_category or "—")
        st.write("**Entities:**", result.entity_count)
        st.caption(f"prompt hash: `{result.prompt_hash}`  (shape-only — never the text)")
    with col2:
        st.subheader("Decision")
        st.write("**Backend:**", f"`{result.backend_id}`  ({result.backend_kind})")
        st.write("**Location:**", result.backend_location)
        st.write("**Rule:**", f"`{result.rule_id}`" if result.rule_id else "default_backend")
        st.write("**Transform:**", result.transform or "none")

    st.caption(f"**Why:** {result.reasoning}")

st.divider()
with st.expander("The policy this demo runs"):
    st.caption("Loaded from `demo/policy.yaml` — first match wins.")
    st.code(
        "privilege               → llama.cpp (local)\n"
        "PHI / MNPI              → Ollama (local)\n"
        "score ≥ 70             → Ollama (local)\n"
        "score ≥ 25             → Anthropic (cloud), redacted first\n"
        "everything else        → OpenAI (cloud)",
        language="text",
    )
    st.markdown(
        "This is the [policy DSL](https://github.com/OgenticAI/ogentic-router/blob/main/docs/POLICY_REFERENCE.md) "
        "— declarative YAML, editable without code."
    )
