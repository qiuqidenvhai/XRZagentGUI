using System;
using System.Collections.Generic;
using System.Text;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public class WinCapture {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr extra);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr extra);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }

  public static List<IntPtr> FindAll(string sub) {
    var list = new List<IntPtr>();
    EnumWindows((h, e) => {
      if (IsWindowVisible(h)) {
        var sb = new StringBuilder(512);
        GetWindowText(h, sb, 512);
        if (sb.ToString().IndexOf(sub, StringComparison.OrdinalIgnoreCase) >= 0) list.Add(h);
      }
      return true;
    }, IntPtr.Zero);
    return list;
  }
  public static Bitmap CaptureWindow(IntPtr h) {
    ShowWindow(h, 9);
    BringWindowToTop(h);
    SetForegroundWindow(h);
    System.Threading.Thread.Sleep(1200);
    RECT r; GetWindowRect(h, out r);
    int w = r.Right - r.Left, hh = r.Bottom - r.Top;
    var bmp = new Bitmap(w, hh);
    using (var g = Graphics.FromImage(bmp)) {
      g.CopyFromScreen(r.Left, r.Top, 0, 0, new Size(w, hh));
    }
    return bmp;
  }
}
