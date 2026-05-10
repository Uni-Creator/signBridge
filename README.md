# signSight

![GitHub Repo stars](https://img.shields.io/github/stars/Uni-Creator/signSight?style=social)
![GitHub forks](https://img.shields.io/github/forks/Uni-Creator/signSight?style=social)
![Accuracy](https://img.shields.io/badge/Top--1%20Accuracy-66.84%25-blue)
![Classes](https://img.shields.io/badge/ISL%20Classes-76-green)
![Model](https://img.shields.io/badge/Backbone-Swin3D--S-orange)

A real-time Indian Sign Language (ISL) recognition system powered by a fine-tuned **Swin3D-S** deep learning model, served via a **FastAPI** backend and paired with a **Flutter** mobile app with live translation and sentence building. 

---

## Demo

https://github.com/user-attachments/assets/033be0e1-8f8f-44bf-a7c8-7467317de273

---

## Results

| Metric | Value |
|---|---|
| Top-1 Accuracy | **66.84%** |
| Macro F1 | 0.638 |
| Weighted F1 | 0.648 |
| Classes | 76 ISL words |
| Test Samples | 187 |
| Random Baseline | 1.3% |

---

## Model Architecture

### Backbone: Swin3D-S (Video Swin Transformer Small)

The model uses a **Swin3D-S** backbone pretrained on **Kinetics-400** for spatiotemporal feature extraction from video clips.

```
Input Video (3 × 16 × 224 × 224)
        ↓
Patch Embedding (Conv3D, 96 channels)
        ↓
Swin Transformer Blocks (4 stages)
  Stage 1: 2 blocks, dim=96,  resolution=8×56×56
  Stage 2: 2 blocks, dim=192, resolution=8×28×28
  Stage 3: 18 blocks, dim=384, resolution=8×14×14
  Stage 4: 2 blocks, dim=768, resolution=8×7×7
        ↓
Adaptive Average Pooling → 768-dim feature vector
        ↓
Linear Classification Head (768 → 76 classes)
```

**Total Parameters:** 33,112,492  
**Trainable Parameters:** 9,510,988 (Stage 3 + Stage 4 + Norm + Head unfrozen)  
**Model Size:** ~126 MB

### Fine-tuning Strategy

- Frozen: Stages 1, 2 and patch embedding
- Unfrozen: `features[6]` (full Stage 3), `norm` layer, classification head
- Loss: CrossEntropyLoss
- Optimizer: AdamW (lr=1e-4)
- Scheduler: ReduceLROnPlateau (factor=0.5, patience=5)
- Mixed Precision: fp16 via `torch.cuda.amp.GradScaler`
- Early Stopping: patience=5

---

## Dataset

**Source:** [Indian Sign Language Words with Landmarks](https://www.kaggle.com/datasets/kaushikyh/indian-sign-language-words-with-landmarks) (Kaggle)

| Split | Samples |
|---|---|
| Train | 745 |
| Validation | 234 |
| Test | 187 |
| **Total** | **1,166** |

**76 ISL word classes:**
`afternoon, animal, bad, beautiful, big, bird, blind, cat, cheap, clothing, cold, cow, curved, deaf, dog, dress, dry, evening, expensive, famous, fast, female, fish, flat, friday, good, happy, hat, healthy, horse, hot, hour, light, long, loose, loud, minute, monday, month, morning, mouse, narrow, new, night, old, pant, pocket, quiet, sad, saturday, second, shirt, shoes, short, sick, skirt, slow, small, suit, sunday, t_shirt, tall, thursday, time, today, tomorrow, tuesday, ugly, warm, wednesday, week, wet, wide, year, yesterday, young`

**Video format:** `.MOV`, variable length, processed to 16 frames at 224×224  
**Preprocessing:** Center crop, rescale (1/255), normalize (mean=0.5, std=0.5)  
**Augmentation (train only):** RandomPerspective, ColorJitter

---

## Training

**Platform:** Kaggle Notebooks  
**Hardware:** NVIDIA Tesla T4 (15GB VRAM)  
**Framework:** PyTorch 2.10.0+cu128  

**Training config:**
```python
BATCH_SIZE  = 32
CLIP_LENGTH = 16       # frames per video
CLIP_SIZE   = 224      # spatial resolution
EPOCHS      = 1000     # with early stopping
LR          = 0.0001
PATIENCE    = 5        # early stopping
SEED        = 42
```

**Training time:** ~3.5 minutes/epoch × ~15 epochs ≈ ~1 hour total

**Pretrained weights:** `Swin3D_S_Weights.KINETICS400_V1` (torchvision)

**Model hosted at:** [huggingface.co/Creator-090/isl-swin3d-model](https://huggingface.co/Creator-090/isl-swin3d-model)

---

## Project Structure

```
.
├── api/                        # FastAPI inference server (HF Space)
│   ├── app.py                  # REST endpoints (/predict, /health, /health/deep)
│   ├── model.py                # Swin3D model + preprocessing + inference
│   ├── requirements.txt        # Dependencies for deployment
│   └── Dockerfile              # Container config for HF Spaces
│
├── backend/                   # Flask + WebSocket server (real-time system layer)
│   ├── saved_captures/        # Temporary video clips generated from frame buffers
│   ├── authentication.py      # Firebase auth (register/login users)
│   ├── history.py             # Store & retrieve translation history (DB layer)
│   ├── main.py                # Core server (REST + WebSocket, frame processing, pipeline orchestration)
│   ├── model.py               # Client wrapper for FastAPI (sends video → gets prediction)
│   ├── requirements.txt       # Backend dependencies (Flask, mediapipe, etc.)
│   ├── hand_landmarker.task   # MediaPipe hand landmark model
│   ├── pose_landmarker_full.task  # MediaPipe pose landmark model
│
├── frontend/                  # Flutter mobile app (client)
│   ├── lib/
│   │   ├── providers/         # State management (app state, predictions, UI sync)
│   │   ├── screens/           # UI screens (e.g., LiveTranslationScreen)
│   │   └── services/          # API + WebSocket communication, sentence builder logic
│   ├── assets/                # Static assets (icons, images)
│   ├── android/               # Android-specific config
│   └── ios/                   # iOS-specific config
│
└── model/                     # Training + experimentation
    ├── is76words.ipynb        # Kaggle notebook (training pipeline)
    ├── checkpoints/           # Saved trained weights (.pt files)
    ├── swin_small_ISL_gpu.py  # Model architecture & training script
    └── dataset/               # ISL gesture dataset
```

---

## API

The FastAPI backend exposes:

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Health check |
| `/health` | GET | Model load status |
| `/health/deep` | GET | Verifies inference works |
| `/predict` | POST | Single video clip → predicted sign |

**Live prediction response:**
```json
{
  "prediction": "happy",
  "confidence": 84.21,
  "top_k": [
    {"class": "happy", "confidence": 84.21},
    {"class": "good",  "confidence": 9.43}
  ],
  "inference_time_ms": 312.5
}
```

---

## Setup Instructions

### Prerequisites

- Python 3.10+
- Flutter SDK ≥ 3.x
- Android Studio or Xcode

### Backend Setup

```bash
git clone https://github.com/Uni-Creator/signSight.git
cd signSight

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r backend/requirements.txt

cd backend
python app.py
# API available at http://localhost:5000
```

### Frontend Setup

```bash
cd frontend
flutter pub get
flutter run
```

### Flutter API Config

Update the API URL in `frontend/lib/services/`:
```dart
static const String API_URL = "https://creator-090-isl-api.hf.space";
// or for local: "http://YOUR_LOCAL_IP:7860"
```

---

## Technologies Used

| Layer | Technology |
|---|---|
| Model backbone | Swin3D-S (torchvision) |
| Pretraining | Kinetics-400 |
| Training framework | PyTorch 2.10 + CUDA 12.8 |
| Mixed precision | torch.cuda.amp (fp16) |
| Video preprocessing | Decord + VivitImageProcessor |
| Backend API | FastAPI + Uvicorn |
| Model hosting | Hugging Face Hub |
| Mobile frontend | Flutter (Dart) |
| Auth & sync | Firebase / Pyrebase4 |
| Text-to-speech | flutter_tts |
| Data augmentation | torchvision v2 transforms |

---

## Live Translation Pipeline

```
Phone Camera
    ↓ (2-sec clips)
Flutter App (LiveISLTranslator)
    ↓ (multipart/form-data POST)
FastAPI /predict
    ↓
Swin3D-S Inference (fp16, CPU on HF Spaces)
    ↓
Smoothing (majority vote over last 3 predictions)
    ↓
SentenceBuilder (confirm word after 2x detection)
    ↓
Display + TTS
```

**Latency (HF Spaces free tier):** ~4–6 sec/clip (CPU inference)

---

## Contributors

<a href="https://github.com/Uni-Creator/signSight/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Uni-Creator/signSight" />
</a>

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push and open a pull request

---

## License

MIT License. See `LICENSE` for details.

---

## Contact

For questions or inquiries, reach out at [abhayr24564@gmail.com](mailto:abhayr24564@gmail.com).
