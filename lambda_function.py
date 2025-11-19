import boto3
import fitz  # PyMuPDF
import os
import uuid
import json
import re

# Clients
s3 = boto3.client('s3')
comprehend_med = boto3.client('comprehendmedical', region_name='ap-southeast-2')
polly = boto3.client('polly')

# buckets / limits
PDF_BUCKET = 'smart-healthcare-assistant'                # where PDFs are uploaded
AUDIO_BUCKET = 'smart-healthcare-assistant-audio'       # where audio + metadata are stored
MAX_CHARS = 20000  # Comprehend Medical limit

# Expanded rule-based mapping of indicators -> specialist
SPECIALTY_RULES = [
    ("Cardiologist", [
        r"\bhigh blood pressure\b", r"\bhypertension\b", r"\bhypertensive\b",
        r"\b(?:bp|b\.p\.)\b", r"\bheart\b", r"\bpalpitation(s)?\b", r"\bchest pain\b",
        r"\b(?:systolic|diastolic)\s*\d{2,3}\b", r"\b(?:mmhg)\b"
    ], "based on blood-pressure / heart-related indicators"),

    ("Cardiologist (Cholesterol/Metabolic)", [
        r"\bcholesterol\b", r"\bLDL\b", r"\bHDL\b", r"\btriglycerid(es)?\b",
        r"\b(?:mg/dl)\b", r"\bhyperlipid(emia|a)\b", r"\bmetabolic syndrome\b"
    ], "based on lipid/metabolic indicators"),

    ("Endocrinologist (Diabetes)", [
        r"\bdiabetes\b", r"\bglucose\b", r"\bHbA1c\b", r"\bHBA1C\b", r"\binsulin\b",
        r"\bblood sugar\b", r"\bfasting sugar\b", r"\bpostprandial\b", r"\bglucose\s*\d{2,3}\b"
    ], "based on diabetes / glucose indicators"),

    ("Endocrinologist (Thyroid)", [
        r"\bthyroid\b", r"\bTSH\b", r"\bT3\b", r"\bT4\b", r"\bhypothyroid(ism)?\b", r"\bhyperthyroid(ism)?\b"
    ], "based on thyroid indicators"),

    ("Endocrinologist (PCOS/Androgen/Metabolic)", [
        r"\bPCOS\b", r"\bpolycystic\b", r"\bandrogen\b", r"\bmenstrual irregularit(y|ies)\b",
        r"\bovary\b", r"\bhyperandrogenism\b", r"\bacne\b.*\b(hirsutism|irregular)\b"
    ], "based on reproductive / androgen / metabolic indicators"),

    ("Nephrologist", [
        r"\bkidney\b", r"\bcreatinine\b", r"\bGFR\b", r"\burea\b", r"\bproteinuria\b",
        r"\bultra?filtrate\b", r"\b(?:eGFR|egfr)\b"
    ], "based on kidney function indicators"),

    ("Hepatologist / Gastroenterologist", [
        r"\bliver\b", r"\bALT\b", r"\bAST\b", r"\bbilirubin\b", r"\bhepatitis\b",
        r"\babdominal pain\b", r"\bnausea\b", r"\bvomit(ing)?\b", r"\bpancreatitis\b"
    ], "based on liver / GI indicators"),

    ("Gastroenterologist (IBD/Colitis)", [
        r"\bdiarrhoea\b", r"\bdiarrhea\b", r"\bconstipation\b", r"\bbloody stool\b",
        r"\binflammatory bowel\b", r"\bcolitis\b"
    ], "based on GI / bowel indicators"),

    ("Pulmonologist", [
        r"\bshortness of breath\b", r"\bdyspnea\b", r"\bdyspnoea\b", r"\bcough\b",
        r"\bwheez(e|ing)?\b", r"\basthma\b", r"\bCOPD\b", r"\bspirometry\b"
    ], "based on respiratory symptoms"),

    ("Neurologist", [
        r"\bseizure(s)?\b", r"\bmigraine\b", r"\bheadache\b", r"\bstroke\b", r"\bneuropathy\b",
        r"\bnumbness\b", r"\bweakness\b", r"\bparalysis\b"
    ], "based on neurological symptoms"),

    ("Orthopedist / Rheumatologist", [
        r"\bjoint pain\b", r"\barthritis\b", r"\brheumatoid\b", r"\bback pain\b",
        r"\bosteoporosis\b", r"\bfracture\b", r"\bsprain\b", r"\bmuscle pain\b"
    ], "based on musculoskeletal / rheumatologic indicators"),

    ("Dermatologist", [
        r"\brash\b", r"\beczema\b", r"\bpsoriasis\b", r"\bitch(ing)?\b", r"\bdermatitis\b",
        r"\blesion\b", r"\bpruritus\b"
    ], "based on skin / dermatological indicators"),

    ("ENT (Otolaryngology)", [
        r"\bENT\b", r"\bsore throat\b", r"\bhoarseness\b", r"\bhearing loss\b",
        r"\bsinus\b", r"\bepistaxis\b", r"\btonsillitis\b", r"\botitis\b"
    ], "based on ear/nose/throat indicators"),

    ("Ophthalmologist", [
        r"\bvision\b", r"\bblurred vision\b", r"\beye pain\b", r"\bconjunctivitis\b",
        r"\bglaucoma\b", r"\bretina\b", r"\bretinopathy\b"
    ], "based on eye / vision indicators"),

    ("Infectious Disease / General Physician", [
        r"\bfever\b", r"\binfection\b", r"\bsepsis\b", r"\bpositive culture\b", r"\bviral\b", r"\bbacterial\b"
    ], "based on infection / fever indicators"),

    ("Hematologist", [
        r"\banemia\b", r"\bhemoglobin\b", r"\bplatelet\b", r"\bleukocytosis\b", r"\bleucocytosis\b"
    ], "based on blood-related indicators"),

    ("Oncologist", [
        r"\btumor\b", r"\btumour\b", r"\bcancer\b", r"\bmetastas(es|is)?\b", r"\bmalignan(t|cy)\b"
    ], "based on cancer-related indicators"),

    ("Psychiatrist / Mental Health", [
        r"\bdepression\b", r"\banxiety\b", r"\binsomnia\b", r"\bself[- ]harm\b", r"\bsuicide\b",
        r"\bmood disorder\b", r"\bpsychosis\b"
    ], "based on mental health indicators"),

    ("Allergist / Immunologist", [
        r"\ballergy\b", r"\banaphylaxis\b", r"\brhinitis\b", r"\bIgE\b", r"\bimmunodeficiency\b"
    ], "based on allergy / immune indicators"),

    ("Sleep Medicine / Pulmonology", [
        r"\bsleep apnea\b", r"\bsleep apnoea\b", r"\bapnea\b", r"\bdaytime somnolence\b", r"\bsnoring\b"
    ], "based on sleep-related indicators"),

    ("Vascular / Vascular Surgeon", [
        r"\bdeep vein thrombosis\b", r"\bDVT\b", r"\bperipheral arterial\b", r"\bclaudication\b"
    ], "based on vascular indicators"),

    ("Dentist / Oral Maxillofacial", [
        r"\btooth\b", r"\bdental\b", r"\boral\b", r"\bgingivitis\b", r"\bperiodontitis\b"
    ], "based on dental / oral health indicators"),

    # fallback low-priority: general review
    ("General Physician", [r".*"], "general review recommended")
]

# --- helpers ---------------------------------------------------

def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }

def pick_specialist(text, entities_texts):
    combined = text + "\n" + " ".join(entities_texts)
    for specialist, patterns, reason in SPECIALTY_RULES:
        for pat in patterns:
            if re.search(pat, combined, flags=re.IGNORECASE):
                return specialist, reason
    return "General Physician", "no clear specialty indicators found; recommend general review"

def safe_detect_entities(chunk):
    try:
        resp = comprehend_med.detect_entities_v2(Text=chunk)
        return resp.get('Entities', [])
    except Exception as e:
        print("ComprehendMedical error:", str(e))
        return []

def synthesize_to_file(text, out_path):
    try:
        resp = polly.synthesize_speech(Text=text, OutputFormat='mp3', VoiceId='Joanna')
        if "AudioStream" in resp:
            with open(out_path, 'wb') as f:
                f.write(resp['AudioStream'].read())
            return True
        else:
            print("Polly did not return AudioStream")
            return False
    except Exception as e:
        print("Polly error:", str(e))
        return False

def generate_presigned_get_url(bucket, key, expires=3600):
    try:
        return s3.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': key}, ExpiresIn=expires)
    except Exception as e:
        print("Presign GET error:", str(e))
        return f"s3://{bucket}/{key}"

# --- S3-triggered PDF processing --------------------------------

def handle_s3_event(event):
    """
    Process S3 event (PDF uploaded) -> extract text -> Comprehend Medical -> Polly -> upload audio + metadata
    Returns dict metadata or raises.
    """
    records = event.get('Records', [])
    if not records:
        raise ValueError("No S3 Records found in event")

    bucket = records[0]['s3']['bucket']['name']
    key = records[0]['s3']['object']['key']

    # Download PDF to /tmp
    local_pdf = f"/tmp/{uuid.uuid4()}.pdf"
    s3.download_file(bucket, key, local_pdf)

    # Extract text from PDF using PyMuPDF (fitz)
    doc = fitz.open(local_pdf)
    pdf_text = ""
    for page in doc:
        pdf_text += page.get_text()
    doc.close()

    if not pdf_text.strip():
        metadata = {
            "s3_pdf": f"s3://{bucket}/{key}",
            "summary_text": "",
            "recommended_specialist": "General Physician",
            "recommendation_reason": "No text found in PDF",
            "audio_s3_url": ""
        }
        meta_key = f"{os.path.splitext(key)[0]}_metadata.json"
        s3.put_object(Bucket=AUDIO_BUCKET, Key=meta_key, Body=json.dumps(metadata).encode('utf-8'),
                      ContentType='application/json')
        return metadata

    # Run Comprehend Medical on chunks
    chunks = [pdf_text[i:i + MAX_CHARS] for i in range(0, len(pdf_text), MAX_CHARS)]
    summary_lines = []
    all_entities = []

    for chunk in chunks:
        ents = safe_detect_entities(chunk)
        for ent in ents:
            ent_text = ent.get('Text', '')
            ent_type = ent.get('Type', '')
            summary_lines.append(f"- {ent_type}: {ent_text}")
            all_entities.append(ent_text)

    summary_text = "Summary of medical entities:\n" + ("\n".join(summary_lines)) if all_entities else "No significant medical entities found in PDF."

    # Decide recommended specialist
    specialist, reason = pick_specialist(pdf_text, [e for e in all_entities])

    recommendation_line = f"Recommended Specialist:\n🩺 {specialist} ({reason})"

    # Full spoken summary text
    spoken_summary = summary_text + "\n\n" + recommendation_line

    # Synthesize to MP3
    audio_file = f"/tmp/{uuid.uuid4()}.mp3"
    success = synthesize_to_file(spoken_summary, audio_file)
    audio_presigned_url = ""
    if not success:
        print("Polly synthesis failed; continuing without audio URL")
    else:
        audio_s3_key = f"{os.path.splitext(key)[0]}_summary.mp3"
        s3.upload_file(audio_file, AUDIO_BUCKET, audio_s3_key)
        audio_presigned_url = generate_presigned_get_url(AUDIO_BUCKET, audio_s3_key, expires=3600)

    # Build metadata and persist it to AUDIO_BUCKET
    metadata = {
        "s3_pdf": f"s3://{bucket}/{key}",
        "summary_text": summary_text,
        "recommended_specialist": specialist,
        "recommendation_reason": reason,
        "audio_s3_url": audio_presigned_url
    }
    meta_key = f"{os.path.splitext(key)[0]}_metadata.json"
    s3.put_object(Bucket=AUDIO_BUCKET, Key=meta_key, Body=json.dumps(metadata).encode('utf-8'),
                  ContentType='application/json')

    return metadata

# --- API handlers (for API Gateway) -------------------------------

def handle_post_submit_pdf(event):
    """
    POST /submit-pdf
    Supports two flows:
    1) If body contains s3Bucket + s3Key -> immediately process that object (calls handle_s3_event via a fake event)
    2) Else expects fileName -> returns presigned PUT url for uploading the PDF to PDF_BUCKET
    """
    try:
        body = json.loads(event.get('body') or "{}")

        # Flow A: process an already-uploaded S3 object
        s3_bucket = body.get('s3Bucket')
        s3_key = body.get('s3Key')
        if s3_bucket and s3_key:
            # build a fake S3 event and call the same handler that S3 would trigger
            fake_event = {
                "Records": [
                    {
                        "s3": {
                            "bucket": {"name": s3_bucket},
                            "object": {"key": s3_key}
                        }
                    }
                ]
            }
            try:
                metadata = handle_s3_event(fake_event)
                return {
                    "statusCode": 200,
                    "headers": cors_headers(),
                    "body": json.dumps(metadata)
                }
            except Exception as e:
                return {
                    "statusCode": 500,
                    "headers": cors_headers(),
                    "body": json.dumps({"error": f"processing error: {str(e)}"})
                }

        # Flow B: return presigned PUT URL for frontend to upload
        file_name = body.get('fileName')
        if not file_name:
            return {
                "statusCode": 400,
                "headers": cors_headers(),
                "body": json.dumps({"error": "fileName required in body if not providing s3Bucket/s3Key"})
            }

        # ensure uploads/ prefix
        file_key = file_name if file_name.startswith('uploads/') else f"uploads/{file_name}"
        
        presigned_url = s3.generate_presigned_url('put_object',
                                                  Params={'Bucket': PDF_BUCKET, 'Key': file_key,
                                                  'ContentType': 'application/pdf'
    },
    ExpiresIn=600
)


        return {
            "statusCode": 200,
            "headers": cors_headers(),
            "body": json.dumps({"uploadUrl": presigned_url, "fileKey": file_key})
        }

    except Exception as e:
        return {"statusCode": 500, "headers": cors_headers(), "body": json.dumps({"error": str(e)})}

def handle_get_metadata(event):
    """
    GET /metadata?metaKey=...
    Returns presigned GET URL for metadata JSON located in AUDIO_BUCKET.
    """
    try:
        params = event.get('queryStringParameters') or {}
        meta_key = params.get('metaKey')
        if not meta_key:
            return {"statusCode": 400, "headers": cors_headers(), "body": json.dumps({"error": "metaKey required"})}

        presigned = s3.generate_presigned_url('get_object', Params={'Bucket': AUDIO_BUCKET, 'Key': meta_key}, ExpiresIn=3600)
        return {"statusCode": 200, "headers": cors_headers(), "body": json.dumps({"metadataUrl": presigned})}
    except Exception as e:
        return {"statusCode": 500, "headers": cors_headers(), "body": json.dumps({"error": str(e)})}

def handle_get_audio(event):
    """
    GET /audio?audioKey=...
    Returns presigned GET URL for MP3 in AUDIO_BUCKET.
    """
    try:
        params = event.get('queryStringParameters') or {}
        audio_key = params.get('audioKey')
        if not audio_key:
            return {"statusCode": 400, "headers": cors_headers(), "body": json.dumps({"error": "audioKey required"})}

        presigned = s3.generate_presigned_url('get_object', Params={'Bucket': AUDIO_BUCKET, 'Key': audio_key}, ExpiresIn=3600)
        return {"statusCode": 200, "headers": cors_headers(), "body": json.dumps({"audioUrl": presigned})}
    except Exception as e:
        return {"statusCode": 500, "headers": cors_headers(), "body": json.dumps({"error": str(e)})}

# --- Lambda entrypoint ----------------------------------------------

def lambda_handler(event, context):
    """
    Unified handler:
    - If event contains S3 records -> process PDF
    - Else treat as API Gateway request -> route by path/method
    """
    # Debug: print event for troubleshooting (CloudWatch)
    try:
        print("EVENT RECEIVED:", json.dumps(event))
    except Exception:
        print("EVENT RECEIVED (non-json)")

    # 1) S3 event
    if event.get('Records') and event['Records'][0].get('s3'):
        try:
            metadata = handle_s3_event(event)
            return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(metadata)}
        except Exception as e:
            print("S3 processing error:", str(e))
            return {"statusCode": 500, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"error": str(e)})}

    # 2) HTTP API / API Gateway
    # Support both REST proxy (httpMethod + path) and HTTP API (requestContext.http.method/rawPath)
    method = event.get('httpMethod') or (event.get('requestContext') or {}).get('http', {}).get('method')
    path = event.get('path') or (event.get('rawPath') or (event.get('requestContext') or {}).get('http', {}).get('path', ''))

    # Normalize
    method = (method or "").upper()
    path = (path or "").lower()

    try:
        # route decisions
        if method == 'OPTIONS':
            return {"statusCode": 204, "headers": cors_headers(), "body": ""}

        # ROUTING: use "in" checks so stage prefixes (like /prod) won't break routes
        if method == "POST" and "/submit-pdf" in path:
            return handle_post_submit_pdf(event)

        if method == "GET" and "/metadata" in path:
            return handle_get_metadata(event)

        if method == "GET" and "/audio" in path:
            return handle_get_audio(event)

        # fallback: unknown route
        return {"statusCode": 404, "headers": cors_headers(), "body": json.dumps({"error": "Route not found", "method": method, "path": path})}
    except Exception as e:
        print("API routing error:", str(e))
        return {"statusCode": 500, "headers": cors_headers(), "body": json.dumps({"error": str(e)})}
