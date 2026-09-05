# AZHURA — Order Flow + Auto OTP + Maintenance FIX6

Perubahan utama:

1. Setelah order berhasil dan nomor diberikan, user hanya melihat **Batal / Refund**.
2. **Cek OTP** tidak tampil saat masih menunggu OTP.
3. Bot melakukan polling OTP otomatis di background. Saat OTP benar-benar diterima, bot mengirim notifikasi otomatis.
4. Setelah OTP diterima, tombol yang tampil:
   - **Cek OTP**
   - **Resend OTP** (sesuai dukungan provider)
   - **Pesanan Selesai**
5. **Menu Utama** tidak tampil pada pesan order awal atau pesan OTP. Menu Utama baru tampil setelah **Pesanan Selesai** ditekan.
6. Tombol **Pesanan Selesai** menyelesaikan order ke provider lalu menutup order lokal.
7. Jika masa aktif habis dan OTP belum diterima, worker otomatis mencoba membatalkan order provider terlebih dahulu. Saldo user hanya dikembalikan setelah pembatalan provider terkonfirmasi.
8. Ditambahkan **Maintenance ON/OFF** di Admin Panel. Saat ON, user tidak bisa membuat order OTP baru; admin tetap dapat mengakses seluruh menu admin.
9. Nama provider tetap tidak ditampilkan pada UI user.
10. Waktu transaksi dan waktu expired tetap menggunakan waktu order pertama; resend tidak mengganti expired awal.

Catatan rate limit: polling RumahOTP dibuat berjarak agar tidak melakukan spam request. Dokumentasi publik RumahOTP menyebut batas 5 request per 10 detik.
