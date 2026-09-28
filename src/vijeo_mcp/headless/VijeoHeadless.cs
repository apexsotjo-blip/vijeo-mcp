// Headless round trip of a Vijeo panel through Vijeo's OWN serializer (EditorDocument, IPersistStorage),
// loaded in-process in a 32-bit host. No window, no Vijeo-Frame.exe, no UI automation.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace VijeoHeadless
{
    [ComImport, Guid("0000000B-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IStorage
    {
        void CreateStream([MarshalAs(UnmanagedType.LPWStr)] string name, uint mode, uint r1, uint r2, out IStream stm);
        void OpenStream([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr r1, uint mode, uint r2, out IStream stm);
        void CreateStorage([MarshalAs(UnmanagedType.LPWStr)] string name, uint mode, uint r1, uint r2, out IStorage stg);
        void OpenStorage([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr prio, uint mode, IntPtr excl, uint r, out IStorage stg);
        void CopyTo(uint ciid, IntPtr rgiid, IntPtr snb, IStorage dest);
        void MoveElementTo([MarshalAs(UnmanagedType.LPWStr)] string name, IStorage dest, [MarshalAs(UnmanagedType.LPWStr)] string newName, uint flags);
        void Commit(uint flags);
        void Revert();
        void EnumElements(uint r1, IntPtr r2, uint r3, out IntPtr enm);
        void DestroyElement([MarshalAs(UnmanagedType.LPWStr)] string name);
        void RenameElement([MarshalAs(UnmanagedType.LPWStr)] string oldName, [MarshalAs(UnmanagedType.LPWStr)] string newName);
        void SetElementTimes([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr c, IntPtr a, IntPtr m);
        void SetClass(ref Guid clsid);
        void SetStateBits(uint bits, uint mask);
        void Stat(out System.Runtime.InteropServices.ComTypes.STATSTG st, uint flag);
    }

    [ComImport, Guid("0000010A-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPersistStorage
    {
        void GetClassID(out Guid clsid);
        [PreserveSig] int IsDirty();
        [PreserveSig] int InitNew(IStorage stg);
        [PreserveSig] int Load(IStorage stg);
        [PreserveSig] int Save(IStorage stg, [MarshalAs(UnmanagedType.Bool)] bool sameAsLoad);
        [PreserveSig] int SaveCompleted(IStorage stg);
        [PreserveSig] int HandsOffStorage();
    }

    public static class Native
    {
        [DllImport("ole32.dll")] public static extern int StgOpenStorage([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr prio, uint mode, IntPtr excl, uint r, out IStorage stg);
        [DllImport("ole32.dll")] public static extern int StgCreateDocfile([MarshalAs(UnmanagedType.LPWStr)] string name, uint mode, uint r, out IStorage stg);
        public const uint READ = 0x0, READWRITE = 0x2, SHARE_EXCLUSIVE = 0x10, SHARE_DENY_WRITE = 0x20, CREATE = 0x1000, TRANSACTED = 0x10000;
    }

    public static class Runner
    {
        static IStorage OpenPath(IStorage root, string path, uint mode)
        {
            IStorage cur = root;
            foreach (var part in path.Split('/'))
            {
                IStorage next; cur.OpenStorage(part, IntPtr.Zero, mode, IntPtr.Zero, 0, out next); cur = next;
            }
            return cur;
        }

        static byte[] ReadStream(IStorage stg, string name)
        {
            IStream s; stg.OpenStream(name, IntPtr.Zero, Native.READ | Native.SHARE_EXCLUSIVE, 0, out s);
            System.Runtime.InteropServices.ComTypes.STATSTG st; s.Stat(out st, 1);
            var buf = new byte[st.cbSize]; IntPtr read = Marshal.AllocHGlobal(4);
            s.Read(buf, buf.Length, read); Marshal.FreeHGlobal(read); Marshal.ReleaseComObject(s);
            return buf;
        }

        // Load the panel storage `panelPath` of `swxcf` into Vijeo's EditorDocument, Save it to `outFile`
        // (a new compound file whose root = the saved panel storage) and report per-stream comparison.
        public static string RoundTrip(string swxcf, string panelPath, string outFile, string streamsCsv)
        {
            var log = new System.Text.StringBuilder();
            IStorage root;
            int hr = Native.StgOpenStorage(swxcf, IntPtr.Zero, Native.READ | Native.SHARE_DENY_WRITE | Native.TRANSACTED, IntPtr.Zero, 0, out root);
            if (hr != 0) return "StgOpenStorage failed 0x" + hr.ToString("X8");
            IStorage panel = OpenPath(root, panelPath, Native.READ | Native.SHARE_EXCLUSIVE);
            Type t = Type.GetTypeFromCLSID(new Guid("451BDCD8-5851-4B24-B663-2A7CA707C9C5"));   // EditorDocument
            object doc = Activator.CreateInstance(t);
            log.AppendLine("EditorDocument created in-process");
            var ps = (IPersistStorage)doc;
            hr = ps.Load(panel);
            log.AppendLine("IPersistStorage.Load -> 0x" + hr.ToString("X8"));
            if (hr != 0) return log.ToString();
            if (File.Exists(outFile)) File.Delete(outFile);
            IStorage outStg;
            hr = Native.StgCreateDocfile(outFile, Native.READWRITE | Native.SHARE_EXCLUSIVE | Native.CREATE, 0, out outStg);
            log.AppendLine("StgCreateDocfile -> 0x" + hr.ToString("X8"));
            hr = ps.Save(outStg, false);
            log.AppendLine("IPersistStorage.Save -> 0x" + hr.ToString("X8"));
            ps.SaveCompleted(null);
            outStg.Commit(0);
            foreach (var name in streamsCsv.Split(','))
            {
                try
                {
                    byte[] a = ReadStream(panel, name), b = ReadStream(outStg, name);
                    bool same = a.Length == b.Length;
                    for (int i = 0; same && i < a.Length; i++) same = a[i] == b[i];
                    log.AppendLine("  " + name + ": source " + a.Length + " bytes, Vijeo-saved " + b.Length + " bytes, identical=" + same);
                }
                catch (Exception e) { log.AppendLine("  " + name + ": " + e.Message); }
            }
            Marshal.ReleaseComObject(outStg); Marshal.ReleaseComObject(doc);
            Marshal.ReleaseComObject(panel); Marshal.ReleaseComObject(root);
            return log.ToString();
        }
    }
}
