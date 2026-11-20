# Bảo mật khi làm việc

> Gắn với lô `lab250_20c5f74a77e2` (seed 7007) và bản bàn giao `lab250_20c5f74a77e2_v2` của nhóm 07.

## 1. Ca USB: hết giờ, muốn chép lô về nhà làm tiếp

Không chép lô ra USB, không gửi qua email cá nhân, Google Drive hay Zalo, và không nhờ người ngoài nhóm làm hộ. Lô 7007 có 250 ảnh, trong đó nhiều ảnh có người thật nhìn rõ mặt (ví dụ `000000023899`). Dữ liệu đã rời khỏi máy công ty thì không còn kiểm soát được ai xem, ai chép tiếp, và không thu hồi được. USB dễ rơi mất; email cá nhân và tài khoản người khác nằm ngoài nhật ký truy cập của dự án, nên khi có sự cố không truy được dữ liệu đã đi đâu. Nhờ người khác làm hộ còn sai thêm một lần: người đó không được cấp quyền với lô này.

Cách đúng: dừng ở mốc đã commit, ghi lại tiến độ (đã review tới ảnh nào, còn FAIL nào của `check_handover.py`) vào ghi chú công việc, rồi làm tiếp trên máy công ty hoặc qua VPN/remote desktop được cấp. Nếu thật sự trễ hạn thì báo trưởng nhóm để xin gia hạn hoặc chia lại việc, không tự mang dữ liệu đi.

## 2. Năm việc: được hay không

- **Mở job được giao trên máy công ty: ĐƯỢC.** Đây là cách làm đúng: đúng người, đúng job, đúng máy có kiểm soát truy cập.
- **Chụp màn hình gửi người quen: KHÔNG.** Ảnh chụp màn hình vẫn là dữ liệu của dự án (ảnh có người, nhãn, tên job). Gửi ra ngoài là làm lộ dữ liệu, kể cả khi chỉ để hỏi cách gán nhãn; muốn hỏi thì hỏi reviewer hoặc trưởng nhóm qua kênh nội bộ.
- **Chép lô ra USB: KHÔNG.** Lý do như mục 1: mất kiểm soát, không truy vết được. Lô v2 của nhóm được bàn giao bằng `manifest.csv` + `.sha256` qua kênh của dự án, không qua thiết bị cá nhân.
- **Dán nhãn thật lên chatbot: KHÔNG.** Công cụ AI bên ngoài có thể lưu và dùng dữ liệu đã dán. Trong lab này nhóm chỉ dùng AI để viết code và chỉ trên dữ liệu COCO công khai (đã ghi trong `prompts.md`); với dữ liệu thật của dự án thì không được dán ảnh, nhãn hay metadata lên chatbot.
- **Đưa tài khoản CVAT cho đồng nghiệp: KHÔNG.** Mỗi tài khoản gắn với một người; nhật ký gán nhãn và cột annotator/reviewer trong `jobs.csv`, `rework_queue.csv` dựa vào đó để biết ai làm gì. Dùng chung tài khoản làm sai truy vết (Pha 5 sẽ báo nhầm người) và lộ quyền truy cập. Đồng nghiệp cần làm thì xin cấp tài khoản và job riêng.

## 3. Ca khuôn mặt: thấy mặt người rõ trong lô, guideline yêu cầu làm mờ

Ví dụ trong lô của nhóm: ảnh `000000023899` (job J10, scene_859) là hai người đang cười trên ghế sofa trong phòng khách, mặt nhìn rõ và không làm mờ; ảnh này đang nằm trong v2 vì chỉ có lỗi panoptic. Ảnh `000000013177` (job J06, scene_899) cũng có người đàn ông đeo kính nhìn rõ mặt khi đang sửa bánh xe.

Việc cần làm: chuyển trạng thái lô sang **Hold** (không Release, không đưa vào train), ghi lại tên các ảnh có mặt chưa làm mờ, và **báo người phụ trách dữ liệu/bảo mật của dự án** cùng trưởng nhóm, kèm danh sách ảnh. Không tự làm mờ và không tự xoá ảnh, vì: (1) làm mờ là thay đổi dữ liệu đã bàn giao, phải đi qua quy trình có người duyệt và ra phiên bản mới có lineage, nếu không sẽ thành ghi đè v1 mà không ai biết; (2) tự làm có thể sót mặt hoặc làm mờ sai vùng, mà không ai kiểm lại; (3) xoá ảnh làm mất bằng chứng và làm sai số liệu QC, `manifest`, `label_summary`; (4) người phụ trách mới quyết được phạm vi: có thể cả lô gốc và các run đã train (như R-021) đều phải xem lại. Trong lúc chờ, không chia sẻ hay chụp lại các ảnh đó.
