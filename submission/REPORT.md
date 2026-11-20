# Báo cáo nhóm 07

Seed luyện tập: **7007**. Lô v1: `lab250_20c5f74a77e2`. Lô v2: `lab250_20c5f74a77e2_v2`.
Mọi số dưới đây lấy từ file thật trong `runs/` (đã chép sang `submission/`) và từ `scripts/score_findings.py` (chỉ in F1 làm tròn một chữ số). Nhóm không đọc `_private/` và không dò từng ảnh bằng điểm.

## 1. Số liệu luyện tập

**F1 theo seed (rule / cross_check / overall).**

| Seed | Bài nộp | rule | cross_check | overall |
|---|---|---:|---:|---:|
| 7007 | chỉ lỗi luật (validator) | ≈ 1.0 | — | ≈ 0.8 |
| 7007 | lỗi luật + **mọi** ứng viên, không xem ảnh | ≈ 1.0 | ≈ 0.6 | ≈ 0.8 |
| **7007** | **lỗi luật + ứng viên đã review (`errors.json` nộp)** | **≈ 1.0** | **≈ 0.9** | **≈ 0.9** |
| 11, 42, 101 | chỉ lỗi luật | ≈ 1.0 | — | ≈ 0.8 |
| 202, 303 | chỉ lỗi luật | ≈ 1.0 | — | ≈ 0.7 |

Lỗi luật đạt F1 ≈ 1.0 trên cả 6 seed (7007 + 5 seed tạo thêm bằng `inject_release.py`). Các seed thêm chỉ dùng để thử validator nên không review ảnh.

**Số ứng viên.**
- Lô sạch (`inject_release.py --seed 5 --no-faults`, 50 ảnh pool `calib`): **0 lỗi luật, 0 ứng viên**.
- Seed 7007: **12 ứng viên trên 10 ảnh** (2 swap, 4 bbox_drift, 2 instance_merge, 4 missing_box). Hàng đợi này đủ nhỏ để cả nhóm xem hết.
- Các seed thử: 10–16 ứng viên trên 8–12 ảnh mỗi lô.

**Ngưỡng đã chọn và cách chọn (không dùng `_private`).** Validator ghép box detection với segment thing panoptic bằng **pixel của mask** (đếm số pixel segment nằm trong box), không chỉ bằng IoU giữa hai bbox. Hai quan sát đo được trên lô sạch quyết định ngưỡng:
1. Với mọi cặp box–segment đúng, **≥ 98% pixel của segment nằm trong box** (box COCO bao trọn vật). Vì vậy `bbox_drift` khi độ phủ < 0,97, hoặc < 0,995 kèm IoU (box, bbox segment) < 0,75 (box bị phóng to rồi lệch).
2. **Không lớp nào có số box khác số segment** (bỏ `iscrowd`). Vì vậy `instance_merge` khi lớp có box > segment và một segment phủ ≥ 2 box; `missing_box` khi lớp có segment > box và có segment ≥ 0,3% ảnh không có box riêng.
3. `swap` khi box và segment khác lớp trùng vị trí (IoU ≥ 0,7) mà chỗ đó không có cặp cùng lớp; người xem ảnh quyết định `class_swap` hay `category_swap`.
4. Bỏ vật < 0,3% diện tích ảnh (mắt người không kiểm được), và bỏ ảnh đã có lỗi luật (mỗi ảnh mang nhiều nhất một lỗi).

Lần đầu nhóm đặt `bbox_drift` là "độ phủ < 0,9 **và** IoU < 0,8" thì bỏ sót các box lệch vừa; xem phân bố độ phủ trên lô sạch và các seed mới nới ra như trên. Trên lô luyện tập có 3 ảnh lệch tự nhiên lặp lại ở mọi seed (`000000000872`, `000000001818`, `000000012748`): giữ trong hàng đợi để người xem ghi `rejected`, không hạ ngưỡng để giấu chúng.

## 2. Vì sao báo hết ứng viên làm F1 giảm?

F1 là trung bình điều hoà của precision và recall, và báo thừa (FP) bị trừ như bỏ sót. Ứng viên chỉ là chỗ **hai nguồn nhãn lệch nhau**, mà detection và panoptic của COCO vốn lệch tự nhiên (vật bị che, panoptic tách một con vật thành hai mảnh, người đứng chồng). Số của nhóm trên seed 7007:
- Nộp **mọi** ứng viên (24 mục: 12 lỗi luật + 12 ứng viên): F1 lỗi gán nhãn **≈ 0,6**. Lý do: 3 ảnh lệch tự nhiên tạo ra 5 ứng viên sai, ảnh có hai ứng viên thì ít nhất một cặp (type, target) sai, và ứng viên `swap` phải đoán `class_swap` hay `category_swap` nên một nửa bị sai loại.
- Nộp sau khi **xem ảnh** (chỉ dòng `confirmed`): F1 lỗi gán nhãn **≈ 0,9**.
- Chỉ nộp lỗi luật: F1 tổng **≈ 0,8** (bỏ hết lỗi gán nhãn); nộp hết ứng viên cũng chỉ **≈ 0,8**, tức báo hết không hơn gì không báo. Xem ảnh mới đẩy F1 tổng lên **≈ 0,9**.

## 3. Quyết định và lineage

- **QC v1: 21/250 = 8,4%** (21 mục trong `errors.json`; mỗi ảnh tính một lần, `scene_leak` tính là một cảnh). Ngưỡng Day 7 là 2% nên **v1: Hold**. Lineage: `decision HOLD` cho `lab250_20c5f74a77e2`, rồi `release lab250_20c5f74a77e2_v2 --parent lab250_20c5f74a77e2`.
- `lineage.py impact` trả `R-021: affected=True action=review_or_retrain`. Run R-021 đã train trên v1, tức đã học từ các box lệch, lớp nhầm, box thiếu và box lặp trong v1. Sửa dataset thành v2 không làm model quên những gì đã học: trọng số vẫn mang lỗi đó. Vì vậy phải đánh giá lại R-021 trên tập đã sửa, và nếu lỗi ảnh hưởng (ví dụ lớp `truck` trong ticket) thì train lại trên v2.
- Ngưỡng tính theo **tỉ lệ ảnh lỗi** chứ không theo số lỗi, vì cùng 21 lỗi thì lô 250 ảnh và lô 25.000 ảnh nghiêm trọng khác nhau.

## 4. Hash chứng minh gì, không chứng minh gì?

**Chứng minh:** file nhận được trùng từng byte với file đã bàn giao. `manifest.csv` ghi sha256 của 480 file trong `images/` và `labels/` của v2, và `.sha256` khoá cả file zip. Ai sửa, thay hay làm hỏng một file thì mã đổi. Hash cũng cho thấy v1 không bị ghi đè: `release_id` của v1 tính từ hash `manifest.json` + `metadata.json`, và `check_handover.py` H1 vẫn PASS. Trong lỗi `wrong_hash` (`000000016010`), hash phát hiện manifest v1 ghi mã của một ảnh khác. Nhóm mở ảnh thấy giải mã được, đúng nội dung (ngựa vằn, bò, đồng cỏ khớp nhãn), nên giữ ảnh trong v2 và ghi mã thật.

**Không chứng minh:** nhãn đúng. Một box lệch hay lớp nhầm vẫn có hash hợp lệ. Hash cũng không nói ai đã gán, file có phải ảnh gốc ban đầu không (chỉ là đúng bản đã ghi vào manifest), hay ảnh có vấn đề bảo mật (mặt người) không. PASS của `check_handover.py` là đúng định dạng và nhất quán, không phải nhãn đúng.

## 5. Truy vết

Ticket: model hay bỏ sót `truck` bị che hoặc ở mép ảnh, thấy ở `scene_370` và `scene_532`. Changelog: v1.3 bắt buộc gán box `truck` bị che > 50% hoặc cắt mép, còn v1.2 cho phép bỏ qua.
- **tier_a** (2 ảnh): ảnh trong `scene_370`, `scene_532`, thuộc job theo guideline **cũ v1.2** (J01–J04 trong `jobs.csv`), có ít nhất một box detection lớp `truck` trong `labels/`: `000000001532`, `000000007088`.
- **tier_b** (5 ảnh): ảnh khác cùng điều kiện (job v1.2, có box `truck`) mà chưa ai báo: `000000000724`, `000000006040`, `000000006723`, `000000007386`, `000000009769`.
- **notify**: người gán và người kiểm của các job chứa ảnh tầng a và b: `A03, A04, Q01, Q02, Q03`.
- `score_findings.py`: tier_a ≈ 1.0, tier_b ≈ 1.0, notify ≈ 1.0 (script `tools/trace_query.py` cũng đạt ≈ 1.0 trên 5 seed thử).

**Vì sao lấy guideline theo job:** guideline là thứ gắn với job lúc giao việc. Cả job làm theo một bản, và người gán làm theo bản của job đó. Phiên bản ghi ở từng ảnh trong `metadata.json` có thể sai, và đó chính là lỗi `guideline_mismatch` (seed 7007 có 2 ảnh như vậy: `000000011615`, `000000024567`). Nếu lấy theo ảnh thì sẽ đưa nhầm hoặc bỏ sót ảnh khỏi tầng truy vết.

## 6. Bug hoặc điểm chưa rõ trong lab (nếu có)

- Không thấy bug trong `scripts/`.
- Bộ ảnh luyện tập có vài ảnh lệch tự nhiên giữa detection và panoptic lặp lại ở mọi seed, trong khi lô sạch (pool `calib`) không có ca nào. Đo nhiễu chỉ trên lô sạch nên đánh giá thấp số ứng viên sai.
- `download_public.py` tạo `data_source/` theo thư mục đang đứng. Chạy từ trong `scripts/` sẽ tạo `scripts/data_source/` mà `.gitignore` gốc (`/data_source`) không chặn: nhóm đã lỡ commit một file `.part` 141 MB và bị GitHub từ chối push. Nhóm đã sửa `.gitignore` thành `data_source/`.
