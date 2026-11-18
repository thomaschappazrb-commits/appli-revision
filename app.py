from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
import streamlit as st
import streamlit.components.v1 as components
from pypdf import PdfReader
from google import genai
from google.genai import types
from groq import Groq

st.set_page_config(
    page_title="Echo",
    page_icon="logo.jpg",
    layout="centered",
    initial_sidebar_state="collapsed",
)

LOGO_URL = "https://raw.githubusercontent.com/thomaschappazrb-commits/appli-revision/main/logo.jpg"

# Injection icône iOS/Android + suppression de " · Streamlit" dans l'onglet
components.html(
    f"""
    <script>
    try {{
        const doc = window.top.document;
        const fixerTitre = () => {{
            if (doc.title !== "Echo") {{
                doc.title = "Echo";
            }}
        }};
        fixerTitre();
        const baliseTitre = doc.querySelector("title");
        if (baliseTitre) {{
            new MutationObserver(fixerTitre).observe(baliseTitre, {{
                childList: true,
                characterData: true,
                subtree: true
            }});
        }}
        doc.querySelectorAll("link[rel*='icon']").forEach(el => el.remove());
        ["apple-touch-icon", "apple-touch-icon-precomposed", "icon"].forEach(rel => {{
            const link = doc.createElement("link");
            link.rel = rel;
            link.href = "{LOGO_URL}";
            doc.head.appendChild(link);
        }});
    }} catch (e) {{}}
    </script>
    """,
    height=0,
)
st.markdown(
    """
    <style>
    #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {
        display: none !important;
    }
    header[data-testid="stHeader"] {
        background: transparent !important;
    }
    .block-container {
        padding-top: 1.2rem !important;
        padding-bottom: 3rem !important;
        max-width: 720px !important;
    }
    .echo-hero {
        display: flex;
        align-items: center;
        gap: 14px;
        padding: 14px 18px;
        background: linear-gradient(135deg, #0F172A 0%, #111827 100%);
        border: 1px solid rgba(56, 189, 248, 0.22);
        border-radius: 18px;
        margin-bottom: 14px;
    }
    .echo-hero img {
        width: 52px;
        height: 52px;
        border-radius: 13px;
        object-fit: cover;
        box-shadow: 0 4px 16px rgba(56, 189, 248, 0.25);
    }
    .echo-title {
        font-size: 1.55rem;
        font-weight: 700;
        color: #F8FAFC;
        margin: 0;
        line-height: 1.15;
        letter-spacing: -0.02em;
    }
    .echo-sub {
        font-size: 0.88rem;
        color: #94A3B8;
        margin: 2px 0 0 0;
    }
    .echo-card {
        background-color: #111827;
        border: 1px solid rgba(148, 163, 184, 0.14);
        border-radius: 16px;
        padding: 16px 18px;
        margin: 10px 0 14px 0;
    }
    .echo-badge {
        display: inline-block;
        font-size: 0.75rem;
        font-weight: 600;
        color: #38BDF8;
        background: rgba(56, 189, 248, 0.12);
        border: 1px solid rgba(56, 189, 248, 0.28);
        padding: 3px 10px;
        border-radius: 999px;
        margin-bottom: 8px;
    }
    .stButton > button {
        width: 100%;
        border-radius: 13px !important;
        padding: 0.62rem 1rem !important;
        font-weight: 600 !important;
        border: 1px solid rgba(56, 189, 248, 0.3) !important;
        transition: all 0.15s ease !important;
    }
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #0284C7 0%, #38BDF8 100%) !important;
        color: #04101E !important;
        border: none !important;
    }
    [data-testid="stFileUploader"] section {
        background-color: #0F172A !important;
        border: 1.5px dashed rgba(56, 189, 248, 0.35) !important;
        border-radius: 16px !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        background-color: #0F172A;
        padding: 5px;
        border-radius: 14px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px !important;
        padding: 8px 12px !important;
        font-weight: 600 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def lire_secret(nom: str) -> str:
    """Lit une clé secrète côté serveur uniquement."""
    try:
        return st.secrets[nom]
    except Exception:
        return ""


def nettoyer_cle(cle: str) -> str:
    return cle.strip().encode("ascii", "ignore").decode("ascii")


MODELES_GEMINI_TEXTE = [
    "gemini-3.8-flash",
    "gemini-3.8-flash-lite",
    "gemini-flash-latest",
]

MODELES_GEMINI_TTS = [
    "gemini-3.8-flash-tts",
    "gemini-2.5-flash-preview-tts",
]

VOIX_EDGE = {
    "Vivienne (Femme — fluide)": "fr-FR-VivienneMultilingualNeural",
    "Rémy (Homme — posé)": "fr-FR-RemyMultilingualNeural",
    "Denise (Femme — claire)": "fr-FR-DeniseNeural",
    "Henri (Homme — classique)": "fr-FR-HenriNeural",
}

VOIX_PROF_EDGE = "fr-FR-RemyMultilingualNeural"
VOIX_ELEVE_EDGE = "fr-FR-VivienneMultilingualNeural"


def nettoyer_texte_pour_oral(texte: str) -> str:
    texte = re.sub(r"[*_#`~]", "", texte)
    texte = re.sub(r"\s+", " ", texte)
    return texte.strip()


def _silence_mp3(duree_ms: int) -> bytes:
    """Génère un silence MP3 valide (MPEG-2 Layer III, 24 kHz, 48 kbps, mono = 24 ms/frame)."""
    nb_frames = max(1, duree_ms // 24)
    frame_vide = b"\xff\xf3\x64\xc4" + (b"\x00" * 140)
    return frame_vide * nb_frames


def _pcm_vers_wav(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    """Encapsule un flux PCM brut 16-bit mono en fichier WAV lisible par tous les navigateurs."""
    if pcm_bytes[:4] == b"RIFF":
        return pcm_bytes
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _segment_edge_tts(texte: str, voix: str, vitesse: str) -> bytes:
    """Génère un segment MP3 via Edge TTS avec 1 tentative de secours."""
    texte_propre = nettoyer_texte_pour_oral(texte)
    if not texte_propre:
        return b""

    derniere_erreur = None
    for _ in range(2):
        tmp_txt = None
        tmp_mp3 = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as f_txt:
                f_txt.write(texte_propre)
                tmp_txt = f_txt.name
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_mp3:
                tmp_mp3 = f_mp3.name

            res = subprocess.run(
                [
                    sys.executable, "-m", "edge_tts",
                    "--voice", voix,
                    f"--rate={vitesse}",
                    "--file", tmp_txt,
                    "--write-media", tmp_mp3,
                ],
                capture_output=True,
                text=True,
                timeout=18,
            )
            if res.returncode == 0 and os.path.exists(tmp_mp3) and os.path.getsize(tmp_mp3) > 0:
                with open(tmp_mp3, "rb") as f:
                    return f.read()
            derniere_erreur = (res.stderr or res.stdout or "réponse vide").strip()
        except Exception as e:
            derniere_erreur = str(e)
        finally:
            for chemin in (tmp_txt, tmp_mp3):
                if chemin and os.path.exists(chemin):
                    try:
                        os.remove(chemin)
                    except OSError:
                        pass
        time.sleep(0.3)

    raise RuntimeError(f"Edge TTS indisponible : {derniere_erreur}")


def _synthese_gemini_tts(script: str, gemini_key: str, est_duo: bool, vitesse: str) -> tuple[bytes, str]:
    """Synthèse vocale naturelle via Gemini TTS (mono ou multi-interlocuteurs)."""
    client = genai.Client(api_key=gemini_key)
    indication_rythme = (
        "Lis à un rythme posé, calme et articulé, avec des respirations naturelles."
        if "-" in vitesse
        else "Lis d'un ton naturel, fluide et engagé, style podcast scientifique."
    )

    if est_duo:
        speech_cfg = types.SpeechConfig(
            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=[
                    types.SpeakerVoiceConfig(
                        speaker="PROF",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Orus")
                        ),
                    ),
                    types.SpeakerVoiceConfig(
                        speaker="ELEVE",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
                        ),
                    ),
                ]
            )
        )
    else:
        speech_cfg = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            )
        )

    # Découpe en blocs de ~2800 caractères pour éviter toute troncature sur les podcasts longs
    lignes = [l.strip() for l in script.splitlines() if l.strip()]
    blocs = []
    courant = []
    taille = 0
    for l in lignes:
        if taille + len(l) > 2800 and courant:
            blocs.append("\n".join(courant))
            courant = [l]
            taille = len(l)
        else:
            courant.append(l)
            taille += len(l)
    if courant:
        blocs.append("\n".join(courant))

    pcm_total = bytearray()
    modele_retenu = None

    for bloc in blocs:
        prompt_tts = f"{indication_rythme}\n\n{bloc}"
        succes_bloc = False
        for modele in MODELES_GEMINI_TTS:
            try:
                rep = client.models.generate_content(
                    model=modele,
                    contents=prompt_tts,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=speech_cfg,
                    ),
                )
                part = rep.candidates[0].content.parts[0]
                data = part.inline_data.data
                if data:
                    if data[:4] == b"RIFF":
                        pcm_total.extend(data[44:])
                    else:
                        pcm_total.extend(data)
                    modele_retenu = modele
                    succes_bloc = True
                    break
            except Exception:
                continue
        if not succes_bloc:
            raise RuntimeError("Gemini TTS indisponible ou quota atteint.")

    return _pcm_vers_wav(bytes(pcm_total)), f"Voix studio ({modele_retenu})"


def synthese_audio_solo(texte: str, gemini_key: str, voix_edge: str, vitesse: str) -> tuple[bytes, str, str]:
    """Retourne (audio_bytes, mime_type, info_moteur)."""
    if gemini_key:
        try:
            wav_bytes, info = _synthese_gemini_tts(nettoyer_texte_pour_oral(texte), gemini_key, False, vitesse)
            return wav_bytes, "audio/wav", info
        except Exception:
            pass

    # Secours Edge TTS découpé par phrases avec micro-pauses naturelles
    phrases = [p.strip() for p in re.split(r"(?<=[.!?])\s+", nettoyer_texte_pour_oral(texte)) if p.strip()]
    if len(phrases) <= 1:
        mp3 = _segment_edge_tts(texte, voix_edge, vitesse)
        return mp3, "audio/mp3", "Voix Edge TTS"

    with ThreadPoolExecutor(max_workers=4) as pool:
        segments = list(pool.map(lambda p: _segment_edge_tts(p, voix_edge, vitesse), phrases))

    pause = _silence_mp3(260)
    assemblage = bytearray()
    for i, seg in enumerate(segments):
        assemblage.extend(seg)
        if i < len(segments) - 1:
            assemblage.extend(pause)
    return bytes(assemblage), "audio/mp3", "Voix Edge TTS (rythme respiré)"


def synthese_audio_dialogue(
    script_brut: str,
    gemini_key: str,
    vitesse: str,
    barre_progression=None,
) -> tuple[bytes, str, str]:
    """Assemble un dialogue à 2 voix avec vraies respirations."""
    lignes_dialogue = []
    for ligne in script_brut.splitlines():
        ligne = ligne.strip()
        match = re.match(r"^(PROF|ELEVE|ÉLÈVE)\s*:\s*(.+)$", ligne, re.IGNORECASE)
        if match:
            role = "PROF" if match.group(1).upper() == "PROF" else "ELEVE"
            replique = nettoyer_texte_pour_oral(match.group(2))
            if replique:
                lignes_dialogue.append((role, replique))

    if len(lignes_dialogue) < 2:
        return synthese_audio_solo(script_brut, gemini_key, VOIX_PROF_EDGE, vitesse)

    # 1. Tentative Gemini Multi-Speaker TTS (rendu conversationnel natif)
    if gemini_key:
        try:
            script_propre = "\n".join(f"{r}: {txt}" for r, txt in lignes_dialogue)
            if barre_progression is not None:
                barre_progression.progress(0.4)
            wav_bytes, info = _synthese_gemini_tts(script_propre, gemini_key, True, vitesse)
            if barre_progression is not None:
                barre_progression.progress(1.0)
            return wav_bytes, "audio/wav", info
        except Exception:
            pass

    # 2. Secours Edge TTS parallélisé avec silences de 480 ms entre interlocuteurs
    total = len(lignes_dialogue)
    segments = [b""] * total

    def tache_segment(idx_item):
        idx, (role, replique) = idx_item
        voix = VOIX_PROF_EDGE if role == "PROF" else VOIX_ELEVE_EDGE
        return idx, _segment_edge_tts(replique, voix, vitesse)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for done, (idx, seg) in enumerate(pool.map(tache_segment, enumerate(lignes_dialogue)), start=1):
            segments[idx] = seg
            if barre_progression is not None:
                barre_progression.progress(done / total)

    pause_tour = _silence_mp3(480)
    audio_complet = bytearray()
    for i, seg in enumerate(segments):
        audio_complet.extend(seg)
        if i < total - 1:
            audio_complet.extend(pause_tour)

    return bytes(audio_complet), "audio/mp3", "Dialogue 2 voix (Edge TTS + pauses)"


def generer_texte_ia(prompt: str, gemini_key: str, groq_key: str, temperature: float = 0.45) -> tuple[str, str]:
    """Appelle Gemini en priorité (contexte long, vulgarisation fine), puis Groq en secours."""
    erreurs = []

    if gemini_key:
        client_gemini = genai.Client(api_key=gemini_key)
        for modele in MODELES_GEMINI_TEXTE:
            try:
                def req():
                    return client_gemini.models.generate_content(
                        model=modele,
                        contents=prompt,
                        config=types.GenerateContentConfig(temperature=temperature),
                    )

                with ThreadPoolExecutor(max_workers=1) as ex:
                    rep = ex.submit(req).result(timeout=25)
                if rep.text:
                    return rep.text, modele
            except Exception as e:
                erreurs.append(f"Gemini {modele}: {e}")

    if groq_key:
        try:
            client_groq = Groq(api_key=groq_key, timeout=22.0)
            modeles_groq = [
                m.id for m in client_groq.models.list().data
                if getattr(m, "active", True)
                and not any(mot in m.id.lower() for mot in ["whisper", "guard", "tts", "playai", "orpheus"])
            ]
            modeles_groq.sort(
                key=lambda x: 0 if any(k in x.lower() for k in ["70b", "llama-4", "qwen", "versatile"]) else 1
            )
            for modele_groq in modeles_groq[:3]:
                try:
                    chat = client_groq.chat.completions.create(
                        model=modele_groq,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature,
                    )
                    return chat.choices[0].message.content, modele_groq
                except Exception as e:
                    erreurs.append(f"Groq {modele_groq}: {e}")
        except Exception as e:
            erreurs.append(f"Groq: {e}")

    raise RuntimeError("Impossible de joindre l'IA.\n" + "\n".join(erreurs))


def construire_carte_cours(texte_complet: str, gemini_key: str, groq_key: str) -> str:
    """Analyse l'intégralité du PDF par blocs pour ne jamais perdre la fin du cours."""
    texte_propre = re.sub(r"\n{3,}", "\n\n", texte_complet).strip()
    if len(texte_propre) <= 18000:
        return texte_propre

    taille_bloc = 16000
    blocs = [texte_propre[i:i + taille_bloc] for i in range(0, len(texte_propre), taille_bloc)][:6]

    def analyser_bloc(idx_bloc):
        idx, contenu = idx_bloc
        prompt = (
            f"Tu extrais la matière pédagogique essentielle de la partie {idx + 1}/{len(blocs)} d'un cours universitaire.\n"
            f"Liste de manière dense et précise :\n"
            f"1. Les concepts et définitions clés\n"
            f"2. Les mécanismes physiques/logiques et relations de cause à effet\n"
            f"3. Les formules ou lois importantes traduites en mots (ex: proportionnel à, inverse du carré...)\n"
            f"4. Les ordres de grandeur, cas limites ou pièges classiques.\n"
            f"Sois exhaustif et rigoureux, sans phrase d'introduction.\n\n"
            f"Extrait du cours :\n{contenu}"
        )
        synthese, _ = generer_texte_ia(prompt, gemini_key, groq_key, temperature=0.2)
        return f"--- PARTIE {idx + 1} DU COURS ---\n{synthese.strip()}"

    with ThreadPoolExecutor(max_workers=3) as pool:
        parties = list(pool.map(analyser_bloc, enumerate(blocs)))

    return "\n\n".join(parties)


def transcrire_audio_groq(audio_bytes: bytes, groq_key: str) -> str:
    """Transcrit la réponse vocale via Whisper sur Groq."""
    client_groq = Groq(api_key=groq_key, timeout=15.0)
    modeles_whisper = [
        m.id for m in client_groq.models.list().data
        if getattr(m, "active", True) and "whisper" in m.id.lower()
    ]
    if not modeles_whisper:
        raise RuntimeError("Aucun modèle Whisper actif sur ton compte Groq.")
    modeles_whisper.sort(key=lambda x: 0 if "turbo" in x.lower() else 1)
    transcription = client_groq.audio.transcriptions.create(
        file=("reponse.wav", audio_bytes),
        model=modeles_whisper[0],
        language="fr",
    )
    return transcription.text


def generer_etape_guidee(
    matiere_cours: str,
    plan: list[str],
    index_etape: int,
    gemini_key: str,
    groq_key: str,
    voix_edge: str,
    vitesse_voix: str,
    plus_simple: bool = False,
):
    etape_actuelle = plan[index_etape]
    numero = index_etape + 1
    total = len(plan)

    if plus_simple:
        consigne = (
            f"L'étudiant n'a pas bien saisi l'étape {numero}/{total} : « {etape_actuelle} ».\n"
            f"Réexplique cette notion BEAUCOUP plus simplement, sans formule ni jargon, à quelqu'un qui court sans feuille.\n"
            f"Utilise une analogie concrète de la vie quotidienne en 75 mots maximum, avec des phrases courtes.\n"
            f"Termine par UNE micro-question ultra simple pour vérifier que l'image mentale est claire."
        )
    else:
        consigne = (
            f"Tu es un excellent pédagogue qui explique ce cours à l'oral à un étudiant qui court (sans schéma ni feuille).\n"
            f"Étape {numero}/{total} : « {etape_actuelle} ».\n"
            f"Explique UNIQUEMENT cette notion en 95 mots maximum.\n"
            f"Règles strictes :\n"
            f"- Attaque directement le sujet dès la première phrase (zéro 'Bonjour' ou 'Dans cette étape').\n"
            f"- Donne d'abord l'intuition physique ou logique, puis le mécanisme précis.\n"
            f"- Traduis toute relation mathématique en français parlé.\n"
            f"- Termine par UNE micro-question courte (réponse en 5 secondes) pour vérifier la compréhension."
        )

    prompt = f"{consigne}\n\nMatière du cours :\n{matiere_cours[:24000]}"
    explication, _ = generer_texte_ia(prompt, gemini_key, groq_key)
    audio_etape, mime_etape, _ = synthese_audio_solo(explication, gemini_key, voix_edge, vitesse_voix)

    st.session_state["guide_tour_id"] += 1
    st.session_state["guide_texte"] = explication.strip()
    st.session_state["guide_audio"] = audio_etape
    st.session_state["guide_mime"] = mime_etape
    st.session_state.pop("guide_feedback_texte", None)
    st.session_state.pop("guide_feedback_audio", None)
    st.session_state.pop("guide_transcription", None)


def reinitialiser_cours():
    cles = [
        "carte_cours", "nb_pages_pdf", "podcast_audio", "podcast_mime", "podcast_script", "podcast_info",
        "plan_cours", "etape_index", "guide_texte", "guide_audio", "guide_mime",
        "guide_feedback_texte", "guide_feedback_audio", "guide_feedback_mime", "guide_transcription",
        "question_actuelle", "question_audio", "question_mime",
        "feedback_texte", "feedback_audio", "feedback_mime", "transcription_user",
        "last_guide_audio_hash", "last_quiz_audio_hash",
    ]
    for c in cles:
        st.session_state.pop(c, None)
    st.session_state["historique_questions"] = []
    st.session_state["a_revoir"] = []
    st.session_state["est_rappel"] = False


for cle, val in [
    ("q_id", 0),
    ("guide_tour_id", 0),
    ("historique_questions", []),
    ("a_revoir", []),
    ("est_rappel", False),
]:
    if cle not in st.session_state:
        st.session_state[cle] = val

# Lecture sécurisée des clés côté serveur
groq_key = nettoyer_cle(lire_secret("GROQ_API_KEY"))
gemini_key = nettoyer_cle(lire_secret("GEMINI_API_KEY"))

# ==========================================
# EN-TÊTE ECHO + RÉGLAGES RAPIDES
# ==========================================
col_hero, col_cfg = st.columns([4, 1.3], vertical_alignment="center")
with col_hero:
    st.markdown(
        f"""
        <div class="echo-hero">
            <img src="{LOGO_URL}" alt="echo">
            <div>
                <p class="echo-title">Echo</p>
                <p class="echo-sub">Tes cours dans les oreilles, taillés pour courir</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with col_cfg:
    with st.popover("⚙️ Audio"):
        vitesse_voix = st.select_slider(
            "Rythme vocal :",
            options=["-20%", "-12%", "-6%", "+0%", "+8%"],
            value="-12%",
        )
        choix_voix = st.selectbox("Voix solo :", list(VOIX_EDGE.keys()))
        voix_edge = VOIX_EDGE[choix_voix]

        if gemini_key:
            st.caption("✨ Moteur : **Gemini + Studio TTS** actif")
        else:
            st.caption("⚡ Moteur : **Groq + Edge TTS** actif")

        if not groq_key and not gemini_key:
            groq_key = nettoyer_cle(st.text_input("Clé Groq (local) :", type="password"))
            gemini_key = nettoyer_cle(st.text_input("Clé Gemini (local) :", type="password"))

if st.session_state["a_revoir"]:
    maintenant = time.time()
    pretes = sum(1 for item in st.session_state["a_revoir"] if maintenant >= item["heure_rappel"])
    col_mem1, col_mem2 = st.columns([3, 1.4], vertical_alignment="center")
    with col_mem1:
        st.caption(f"🧠 File mémoire : **{len(st.session_state['a_revoir'])}** notion(s) ({pretes} prête(s))")
    with col_mem2:
        if st.button("⏩ +10 min", key="sim_rappel"):
            for item in st.session_state["a_revoir"]:
                item["heure_rappel"] = time.time() - 1
            st.rerun()

# ==========================================
# CHARGEMENT DU COURS (100 % DU PDF)
# ==========================================
uploaded_file = st.file_uploader("Dépose ton cours (PDF ou TXT)", type=["pdf", "txt"])

if uploaded_file is not None:
    if st.session_state.get("fichier_actuel") != uploaded_file.name:
        reinitialiser_cours()
        st.session_state["fichier_actuel"] = uploaded_file.name

    if uploaded_file.name.endswith(".pdf"):
        reader = PdfReader(uploaded_file)
        nb_pages = len(reader.pages)
        texte_brut = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        nb_pages = 1
        texte_brut = uploaded_file.read().decode("utf-8", errors="ignore")

    if not texte_brut.strip():
        st.warning("Aucun texte détecté dans ce fichier.")
    else:
        if "carte_cours" not in st.session_state:
            with st.status(f"Analyse complète de {uploaded_file.name} ({nb_pages} page(s))...", expanded=False) as st_load:
                try:
                    st.session_state["carte_cours"] = construire_carte_cours(texte_brut, gemini_key, groq_key)
                    st.session_state["nb_pages_pdf"] = nb_pages
                    st_load.update(label=f"Cours prêt ({nb_pages} page(s) analysées en entier)", state="complete")
                except Exception as e:
                    st_load.update(label="Erreur d'analyse", state="error")
                    st.error(str(e))

        matiere_cours = st.session_state.get("carte_cours", texte_brut[:24000])

        tab_podcast, tab_guide, tab_quiz = st.tabs([
            "🎙️ Podcast",
            "🧭 Cours pas-à-pas",
            "🔥 Crash-test",
        ])

        # ==========================================
        # MODE 1 : PODCAST HORS-LIGNE HAUTE QUALITÉ
        # ==========================================
        with tab_podcast:
            st.markdown(
                """
                <div class="echo-card">
                    <span class="echo-badge">ÉPISODE SUR MESURE</span>
                    <div style="color:#CBD5E1;font-size:0.9rem;">
                        Couvre l'intégralité du document : intuition concrète, mécanismes clés, traduction orale des formules et pièges classiques.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if hasattr(st, "segmented_control"):
                format_audio = st.segmented_control(
                    "Format :",
                    ["Dialogue (2 voix)", "Monologue (1 voix)"],
                    default="Dialogue (2 voix)",
                ) or "Dialogue (2 voix)"
                duree_podcast = st.segmented_control(
                    "Durée :",
                    ["⚡ Flash (5 min)", "🎧 Approfondi (12 min)"],
                    default="⚡ Flash (5 min)",
                ) or "⚡ Flash (5 min)"
            else:
                format_audio = st.radio("Format :", ["Dialogue (2 voix)", "Monologue (1 voix)"], horizontal=True)
                duree_podcast = st.radio("Durée :", ["⚡ Flash (5 min)", "🎧 Approfondi (12 min)"], horizontal=True)

            if st.button("🎧 Générer l'épisode audio", type="primary"):
                if not gemini_key and not groq_key:
                    st.warning("Configure une clé API côté serveur.")
                else:
                    with st.status("Création de ton épisode...", expanded=True) as status:
                        try:
                            est_duo = "2 voix" in format_audio
                            est_long = "Approfondi" in duree_podcast

                            regles_pedago = (
                                "RÈGLES PÉDAGOGIQUES STRICTES :\n"
                                "- INTERDICTION de commencer par 'Bonjour', 'Bienvenue dans ce podcast' ou toute formule creuse. "
                                "Attaque dès la 1re phrase par le problème concret que ce cours résout.\n"
                                "- INTERDICTION des répliques vides type 'C'est une super question !' ou 'Tout à fait !'.\n"
                                "- Structure l'épisode en 4 temps : 1) Vue d'ensemble et utilité concrète, 2) Mécanismes fondamentaux pas à pas, "
                                "3) Explication intuitive des lois/formules à l'oral (jamais de symboles bruts), 4) Les 2 pièges classiques d'examen.\n"
                                "- Aucun astérisque, aucune liste à puces, aucun markdown : uniquement du français oral naturel."
                            )

                            if est_duo:
                                cible_longueur = (
                                    "26 à 30 répliques riches (3 à 4 phrases par réplique, environ 1500 mots) couvrant TOUTES les parties du cours."
                                    if est_long
                                    else "14 à 16 répliques percutantes (2 à 3 phrases par réplique, environ 700 mots) couvrant l'essentiel de tout le cours."
                                )
                                prompt = (
                                    f"Écris un script de podcast scientifique en français à 2 voix pour un étudiant de niveau ingénieur/master qui court sans feuille.\n"
                                    f"Personnages :\n"
                                    f"- PROF : expert calme, limpide, qui donne des images mentales précises.\n"
                                    f"- ELEVE : étudiante vive qui pose les vraies questions de compréhension ('Attend, ça veut dire que si X double, Y est divisé par quatre ?'), "
                                    f"reformule avec ses mots et fait le lien entre les notions.\n\n"
                                    f"{regles_pedago}\n"
                                    f"Longueur cible : {cible_longueur}\n"
                                    f"FORMAT STRICT : chaque ligne commence exactement par 'PROF:' ou 'ELEVE:' suivi du texte parlé.\n\n"
                                    f"Matière complète du cours :\n{matiere_cours[:32000]}"
                                )
                            else:
                                cible_longueur = (
                                    "environ 1400 mots structurés en paragraphes fluides avec transitions naturelles, couvrant tout le cours."
                                    if est_long
                                    else "environ 650 mots percutants et fluides, couvrant tout le cours."
                                )
                                prompt = (
                                    f"Écris un monologue de podcast pédagogique en français pour un étudiant qui court sans schéma.\n"
                                    f"{regles_pedago}\n"
                                    f"Longueur cible : {cible_longueur}\n\n"
                                    f"Matière complète du cours :\n{matiere_cours[:32000]}"
                                )

                            st.write("1/2 — Écriture du script sur l'ensemble du cours...")
                            script_audio, modele_txt = generer_texte_ia(prompt, gemini_key, groq_key, temperature=0.5)

                            st.write("2/2 — Production audio...")
                            if est_duo:
                                barre = st.progress(0.0)
                                audio_bytes, mime_audio, info_voix = synthese_audio_dialogue(
                                    script_audio, gemini_key, vitesse_voix, barre
                                )
                            else:
                                audio_bytes, mime_audio, info_voix = synthese_audio_solo(
                                    script_audio, gemini_key, voix_edge, vitesse_voix
                                )

                            st.session_state["podcast_audio"] = audio_bytes
                            st.session_state["podcast_mime"] = mime_audio
                            st.session_state["podcast_script"] = script_audio
                            st.session_state["podcast_info"] = f"{modele_txt} • {info_voix}"
                            status.update(label="Épisode prêt !", state="complete", expanded=False)
                        except Exception as e:
                            status.update(label="Erreur", state="error")
                            st.error(str(e))

            if "podcast_audio" in st.session_state:
                ext = "wav" if "wav" in st.session_state["podcast_mime"] else "mp3"
                st.caption(f"✨ {st.session_state['podcast_info']}")
                st.audio(st.session_state["podcast_audio"], format=st.session_state["podcast_mime"])
                st.download_button(
                    label=f"⬇️ Sauvegarder hors-ligne (.{ext})",
                    data=st.session_state["podcast_audio"],
                    file_name=f"echo_podcast.{ext}",
                    mime=st.session_state["podcast_mime"],
                )
                with st.expander("📄 Lire le script de l'épisode"):
                    st.write(st.session_state["podcast_script"])

        # ==========================================
        # MODE 2 : COURS GUIDÉ PAS-À-PAS (AUTO-MICRO)
        # ==========================================
        with tab_guide:
            if "plan_cours" not in st.session_state:
                st.markdown(
                    """
                    <div class="echo-card">
                        <span class="echo-badge">PROGRESSION ACTIVE</span>
                        <div style="color:#CBD5E1;font-size:0.9rem;">
                            Le cours est découpé en 5 briques digestes. À chaque étape, réponds au micro (envoi automatique dès que tu coupes) ou dis simplement <b>« plus simple »</b> ou <b>« suivant »</b>.
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("🚀 Lancer le cours pas-à-pas", type="primary"):
                    if not groq_key and not gemini_key:
                        st.warning("Configure une clé API côté serveur.")
                    else:
                        with st.status("Structuration du parcours...", expanded=True) as status:
                            try:
                                prompt_plan = (
                                    f"Découpe l'intégralité de ce cours en 5 mini-étapes progressives pour un enseignement oral sans support visuel.\n"
                                    f"Étape 1 : vue d'ensemble et problème résolu par le cours.\n"
                                    f"Étapes 2 à 5 : les concepts majeurs du début à la fin du document, du plus intuitif au plus technique.\n"
                                    f"FORMAT STRICT : uniquement 5 lignes numérotées (1. ... à 5. ...), sans texte autour.\n\n"
                                    f"Matière :\n{matiere_cours[:28000]}"
                                )
                                plan_brut, _ = generer_texte_ia(prompt_plan, gemini_key, groq_key, temperature=0.3)
                                etapes = [
                                    re.sub(r"^\s*[\d\-•*]+[\.\)\-:]?\s*", "", l).strip()
                                    for l in plan_brut.splitlines()
                                    if len(re.sub(r"^\s*[\d\-•*]+[\.\)\-:]?\s*", "", l).strip()) > 5
                                ]
                                st.session_state["plan_cours"] = (etapes or ["Vue d'ensemble", "Mécanismes clés", "Synthèse"])[:5]
                                st.session_state["etape_index"] = 0

                                generer_etape_guidee(
                                    matiere_cours, st.session_state["plan_cours"], 0,
                                    gemini_key, groq_key, voix_edge, vitesse_voix
                                )
                                status.update(label="Parcours prêt !", state="complete", expanded=False)
                                st.rerun()
                            except Exception as e:
                                status.update(label="Erreur", state="error")
                                st.error(str(e))
            else:
                plan = st.session_state["plan_cours"]
                idx = st.session_state["etape_index"]

                if idx >= len(plan):
                    st.success("🎉 Parcours terminé ! Passe sur l'onglet Crash-test pour ancrer ce que tu viens de voir.")
                    if st.button("🔄 Recommencer le parcours"):
                        st.session_state.pop("plan_cours", None)
                        st.rerun()
                else:
                    st.progress((idx + 1) / len(plan))
                    st.markdown(
                        f"""
                        <div class="echo-card">
                            <span class="echo-badge">ÉTAPE {idx + 1} / {len(plan)}</span>
                            <div style="font-size:1.05rem;font-weight:600;color:#F8FAFC;">{plan[idx]}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    a_un_feedback = "guide_feedback_audio" in st.session_state
                    st.audio(
                        st.session_state["guide_audio"],
                        format=st.session_state.get("guide_mime", "audio/mp3"),
                        autoplay=not a_un_feedback,
                    )

                    with st.expander("📄 Lire l'explication écrite"):
                        st.write(st.session_state["guide_texte"])

                    audio_guide = st.audio_input(
                        "🎤 Appuie pour parler (envoi auto à l'arrêt) :",
                        key=f"guide_micro_{st.session_state['guide_tour_id']}",
                    )

                    # Envoi automatique dès qu'un nouvel enregistrement est terminé
                    if audio_guide is not None:
                        raw_bytes = audio_guide.getvalue()
                        h = hashlib.md5(raw_bytes).hexdigest()
                        if st.session_state.get("last_guide_audio_hash") != h:
                            st.session_state["last_guide_audio_hash"] = h
                            with st.status("Écoute et analyse...", expanded=False) as status:
                                try:
                                    texte_dit = transcrire_audio_groq(raw_bytes, groq_key)
                                    texte_lower = texte_dit.lower().strip()
                                    nb_mots = len(texte_lower.split())

                                    if nb_mots <= 5 and any(c in texte_lower for c in ["suivant", "étape suivante", "passe"]):
                                        st.session_state["etape_index"] += 1
                                        if st.session_state["etape_index"] < len(plan):
                                            generer_etape_guidee(
                                                matiere_cours, plan, st.session_state["etape_index"],
                                                gemini_key, groq_key, voix_edge, vitesse_voix
                                            )
                                        st.rerun()

                                    if nb_mots <= 7 and any(c in texte_lower for c in ["plus simple", "pas compris", "répète", "explique autrement"]):
                                        generer_etape_guidee(
                                            matiere_cours, plan, idx,
                                            gemini_key, groq_key, voix_edge, vitesse_voix, plus_simple=True
                                        )
                                        st.rerun()

                                    prompt_coach = (
                                        f"Tu dialogues à l'oral avec un étudiant sur l'étape « {plan[idx]} ».\n"
                                        f"Ton explication et ta question : {st.session_state['guide_texte']}\n"
                                        f"Ce que l'étudiant vient de dire : {texte_dit}\n"
                                        f"Cours : {matiere_cours[:20000]}\n\n"
                                        f"Réponds en 3 phrases courtes et naturelles (zéro puce, zéro symbole brut) : "
                                        f"valide ce qui est juste, corrige ou éclaire avec une image simple si besoin, "
                                        f"et dis-lui s'il peut passer à la suite."
                                    )
                                    feedback, _ = generer_texte_ia(prompt_coach, gemini_key, groq_key)
                                    audio_fb, mime_fb, _ = synthese_audio_solo(feedback, gemini_key, voix_edge, vitesse_voix)

                                    st.session_state["guide_transcription"] = texte_dit
                                    st.session_state["guide_feedback_texte"] = feedback.strip()
                                    st.session_state["guide_feedback_audio"] = audio_fb
                                    st.session_state["guide_feedback_mime"] = mime_fb
                                    status.update(label="Réponse prête", state="complete")
                                    st.rerun()
                                except Exception as e:
                                    status.update(label="Erreur", state="error")
                                    st.error(str(e))

                    col_b, col_c = st.columns(2)
                    with col_b:
                        if st.button("💡 Plus simple"):
                            with st.spinner("Reformulation..."):
                                generer_etape_guidee(
                                    matiere_cours, plan, idx,
                                    gemini_key, groq_key, voix_edge, vitesse_voix, plus_simple=True
                                )
                                st.rerun()
                    with col_c:
                        label_suiv = "➡️ Étape suivante" if idx + 1 < len(plan) else "🏁 Terminer"
                        if st.button(label_suiv, type="primary"):
                            st.session_state["etape_index"] += 1
                            if st.session_state["etape_index"] < len(plan):
                                with st.spinner("Étape suivante..."):
                                    generer_etape_guidee(
                                        matiere_cours, plan, st.session_state["etape_index"],
                                        gemini_key, groq_key, voix_edge, vitesse_voix
                                    )
                            st.rerun()

                    if "guide_feedback_texte" in st.session_state:
                        st.caption(f"Toi : *« {st.session_state['guide_transcription']} »*")
                        st.audio(
                            st.session_state["guide_feedback_audio"],
                            format=st.session_state.get("guide_feedback_mime", "audio/mp3"),
                            autoplay=True,
                        )
                        with st.expander("📄 Lire la réponse du prof"):
                            st.write(st.session_state["guide_feedback_texte"])

        # ==========================================
        # MODE 3 : CRASH-TEST & RAPPEL +10 MIN
        # ==========================================
        with tab_quiz:
            if hasattr(st, "segmented_control"):
                difficulte = st.segmented_control(
                    "Intensité :",
                    ["🟢 Footing", "🟠 Standard", "🔴 Examen"],
                    default="🟠 Standard",
                ) or "🟠 Standard"
            else:
                difficulte = st.radio("Intensité :", ["🟢 Footing", "🟠 Standard", "🔴 Examen"], horizontal=True)

            notions_pretes = [
                item for item in st.session_state["a_revoir"]
                if time.time() >= item["heure_rappel"]
            ]
            label_bouton = (
                f"🔁 Réviser la notion ratée ({len(notions_pretes)} prête(s))"
                if notions_pretes
                else "🎯 Nouvelle question"
            )

            if st.button(label_bouton, type="primary"):
                if not groq_key and not gemini_key:
                    st.warning("Configure une clé API côté serveur.")
                else:
                    with st.status("Préparation de la question...", expanded=False) as status:
                        try:
                            if "Footing" in difficulte:
                                style_q = "Pose un Vrai/Faux rapide (commence par 'Vrai ou faux :') ou une question qui se répond en 3 mots."
                            elif "Standard" in difficulte:
                                style_q = "Pose une question claire demandant d'expliquer en 2 phrases un mécanisme du cours."
                            else:
                                style_q = "Pose une question exigeante de niveau examen : cas limite, piège classique ou lien entre deux notions."

                            maintenant = time.time()
                            idx_rev = next(
                                (i for i, item in enumerate(st.session_state["a_revoir"]) if maintenant >= item["heure_rappel"]),
                                None,
                            )

                            if idx_rev is not None:
                                notion_ratee = st.session_state["a_revoir"].pop(idx_rev)
                                st.session_state["est_rappel"] = True
                                prompt_q = (
                                    f"L'étudiant avait raté cette notion il y a 10 minutes : « {notion_ratee['question']} ».\n"
                                    f"Repose UNE SEULE question courte (2 phrases max) sur ce même concept, reformulée légèrement. {style_q}\n"
                                    f"Zéro puce, zéro symbole mathématique brut.\n\nMatière :\n{matiere_cours[:24000]}"
                                )
                            else:
                                st.session_state["est_rappel"] = False
                                deja = "\n".join(f"- {q}" for q in st.session_state["historique_questions"][-8:])
                                prompt_q = (
                                    f"Pose UNE SEULE question orale courte (2 phrases max) portant sur n'importe quelle partie du cours. {style_q}\n"
                                    f"Zéro puce, zéro symbole brut. Ne repose pas ces questions :\n{deja}\n\n"
                                    f"Matière :\n{matiere_cours[:24000]}"
                                )

                            question_texte, _ = generer_texte_ia(prompt_q, gemini_key, groq_key)
                            audio_q, mime_q, _ = synthese_audio_solo(question_texte, gemini_key, voix_edge, vitesse_voix)

                            st.session_state["q_id"] += 1
                            st.session_state["question_actuelle"] = question_texte.strip()
                            st.session_state["question_audio"] = audio_q
                            st.session_state["question_mime"] = mime_q
                            st.session_state["historique_questions"].append(question_texte.strip())
                            st.session_state.pop("feedback_audio", None)
                            st.session_state.pop("feedback_texte", None)
                            st.session_state.pop("transcription_user", None)
                            status.update(label="Question prête", state="complete")
                            st.rerun()
                        except Exception as e:
                            status.update(label="Erreur", state="error")
                            st.error(str(e))

            if "question_actuelle" in st.session_state:
                a_un_fb_quiz = "feedback_audio" in st.session_state
                badge_q = "🔁 RAPPEL MÉMOIRE (+10 MIN)" if st.session_state["est_rappel"] else "QUESTION ORALE"
                st.markdown(
                    f"""
                    <div class="echo-card">
                        <span class="echo-badge">{badge_q}</span>
                        <div style="font-size:1.02rem;color:#F8FAFC;">{st.session_state['question_actuelle']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.audio(
                    st.session_state["question_audio"],
                    format=st.session_state.get("question_mime", "audio/mp3"),
                    autoplay=not a_un_fb_quiz,
                )

                audio_rep = st.audio_input(
                    "🎤 Réponds à voix haute (envoi auto à l'arrêt) :",
                    key=f"micro_{st.session_state['q_id']}",
                )

                if audio_rep is not None:
                    raw_rep = audio_rep.getvalue()
                    h_rep = hashlib.md5(raw_rep).hexdigest()
                    if st.session_state.get("last_quiz_audio_hash") != h_rep:
                        st.session_state["last_quiz_audio_hash"] = h_rep
                        with st.status("Correction en cours...", expanded=False) as status:
                            try:
                                texte_dit = transcrire_audio_groq(raw_rep, groq_key)
                                st.session_state["transcription_user"] = texte_dit

                                prompt_eval = (
                                    f"Tu évalues la réponse orale d'un étudiant (niveau : {difficulte}).\n"
                                    f"Question : {st.session_state['question_actuelle']}\n"
                                    f"Réponse de l'étudiant : {texte_dit}\n"
                                    f"Cours : {matiere_cours[:22000]}\n\n"
                                    f"Consignes :\n"
                                    f"1. Première ligne uniquement : 'STATUT: REUSSI' ou 'STATUT: A_REVOIR'.\n"
                                    f"2. Lignes suivantes : retour oral en 3 phrases courtes et directes (zéro puce ni symbole brut). "
                                    f"Dis nettement si c'est juste ou faux, valide ce qui est bon et complète précisément."
                                )
                                retour_brut, _ = generer_texte_ia(prompt_eval, gemini_key, groq_key)
                                lignes = retour_brut.strip().splitlines()
                                est_a_revoir = "A_REVOIR" in (lignes[0].upper() if lignes else "")
                                retour_oral = "\n".join(lignes[1:]).strip() if len(lignes) > 1 else retour_brut

                                if est_a_revoir:
                                    st.session_state["a_revoir"].append({
                                        "question": st.session_state["question_actuelle"],
                                        "heure_rappel": time.time() + 600,
                                    })

                                audio_fb, mime_fb, _ = synthese_audio_solo(retour_oral, gemini_key, voix_edge, vitesse_voix)
                                st.session_state["feedback_texte"] = retour_oral
                                st.session_state["feedback_statut"] = "À revoir dans 10 min" if est_a_revoir else "Validé"
                                st.session_state["feedback_audio"] = audio_fb
                                st.session_state["feedback_mime"] = mime_fb
                                status.update(label="Correction prête", state="complete")
                                st.rerun()
                            except Exception as e:
                                status.update(label="Erreur", state="error")
                                st.error(str(e))

            if "feedback_texte" in st.session_state:
                st.caption(f"Toi : *« {st.session_state['transcription_user']} »*")
                if st.session_state["feedback_statut"] == "Validé":
                    st.success(f"**{st.session_state['feedback_statut']} :** {st.session_state['feedback_texte']}")
                else:
                    st.warning(f"**{st.session_state['feedback_statut']} :** {st.session_state['feedback_texte']}")
                st.audio(
                    st.session_state["feedback_audio"],
                    format=st.session_state.get("feedback_mime", "audio/mp3"),
                    autoplay=True,
                )
