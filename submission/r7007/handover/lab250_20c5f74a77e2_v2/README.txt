Release: lab250_20c5f74a77e2_v2
Parent: lab250_20c5f74a77e2
Số ảnh: 240
Số nhãn: 1669
Guideline: v1.2 (J01-J04), v1.3 (J05-J10)
QC v1: 21/250 = 8,4%
Kết luận v1: Hold — 21 ảnh/cảnh lỗi trên 250 ảnh = 8.4% vượt ngưỡng 2%; lỗi theo loại: bbox_drift 4, bbox_out_of_bounds 1, category_swap 1, class_swap 1, duplicate_box 1, guideline_mismatch 2, instance_merge 2, invalid_category_id 2, invalid_class 2, missing_box 1, missing_image 1, scene_leak 1, thing_overlap 1, wrong_hash 1
Kết luận v2: Release — sản phẩm detection 240 ảnh: đã tách 10 ảnh lỗi nhãn detection/thiếu file sang rework, sửa hash/guideline/split ngay trong v2; còn 6 ảnh lỗi panoptic (không ảnh hưởng detection) chờ team panoptic sửa
Ghi chú: wrong_hash 000000016010: đã mở ảnh, giải mã được và đúng nội dung so với nhãn, chỉ manifest v1 ghi sai mã; v2 giữ ảnh và ghi sha256 thật trong manifest.csv | guideline_mismatch 000000011615, 000000024567: metadata ảnh ghi sai phiên bản; truyvet.csv lấy guideline theo job trong jobs.csv | scene_leak scene_593: cảnh nằm ở cả train và val; đưa cả cảnh về train (chuyển 000000020333) để không rò train/val | rework (rời v2, giao lại đúng người theo jobs.csv): A01/Q01: 000000002685, 000000003845, 000000004765; A03/Q01: 000000023126; A03/Q02: 000000005992; A04/Q01: 000000000785, 000000000872, 000000020247; A04/Q02: 000000013177; A05/Q01: 000000016451 | lỗi chỉ ở panoptic, nhãn detection vẫn đúng nên ảnh ở lại v2, báo team panoptic: 000000006471 (category_swap), 000000011511 (thing_overlap), 000000015079 (instance_merge), 000000018491 (invalid_category_id), 000000018833 (invalid_category_id), 000000023899 (instance_merge) | nhãn detection trong v2 giữ nguyên byte của v1, không sửa tay
