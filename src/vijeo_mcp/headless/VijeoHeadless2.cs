// Headless experiment 2: open the project through Vijeo's own storage manager (SwxLocal / ISwxStorage)
// and host EditorDocument the way Vijeo does (ITISDocument.Initialize with the panel ISwxStorage).
using System;
using System.Reflection;
using System.Runtime.InteropServices;

namespace VijeoHeadless2
{
    [ComImport, Guid("B7F43BD3-8342-40E0-B85E-05D10B56D84C"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface ITISDocument
    {
        [PreserveSig] int SetID(int id);
        [PreserveSig] int GetID(out int id);
        [PreserveSig] int Initialize(IntPtr hwnd, int tabId, int nodeId, IntPtr pISwxStorage, IntPtr pITISTarget);
        [PreserveSig] int Shutdown(int removeFromWorkspace);
        [PreserveSig] int IsDirty();
        [PreserveSig] int CanSave();
        [PreserveSig] int Save();
        [PreserveSig] int Revert();
        [PreserveSig] int Commit();
        [PreserveSig] int BuildTargetMenu(out IntPtr hmenu);
    }

    [ComImport, Guid("0000010A-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPersistStorage
    {
        void GetClassID(out Guid clsid);
        [PreserveSig] int IsDirty();
        [PreserveSig] int InitNew(IntPtr stg);
        [PreserveSig] int Load(IntPtr stg);
        [PreserveSig] int Save(IntPtr stg, [MarshalAs(UnmanagedType.Bool)] bool sameAsLoad);
        [PreserveSig] int SaveCompleted(IntPtr stg);
        [PreserveSig] int HandsOffStorage();
    }

    [ComImport, Guid("FEAA1D2D-9FD4-4D41-B29E-0F8D14ABCE88"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface ISwxCompoundFileManager
    {
        [PreserveSig] int CreateSwxCompoundFile([MarshalAs(UnmanagedType.BStr)] string key, [MarshalAs(UnmanagedType.BStr)] string name, uint mode, uint flags, out IntPtr stg);
        [PreserveSig] int OpenSwxCompoundFile([MarshalAs(UnmanagedType.BStr)] string key, [MarshalAs(UnmanagedType.BStr)] string name, uint mode, uint flags, [MarshalAs(UnmanagedType.BStr)] string version, out IntPtr stg);
        [PreserveSig] int InitializeCompoundFileManager();
        [PreserveSig] int CleanupTemporarySwxStorageFile([MarshalAs(UnmanagedType.BStr)] string key, [MarshalAs(UnmanagedType.BStr)] string name);
    }

    [ComImport, Guid("194683E4-C55C-4EB8-9381-02BB091E1695"), InterfaceType(ComInterfaceType.InterfaceIsDual)]
    public interface ISwxStorage
    {
        [PreserveSig] int get_Name([MarshalAs(UnmanagedType.BStr)] out string name);
        [PreserveSig] int get_ClassStr([MarshalAs(UnmanagedType.BStr)] out string s);
        [PreserveSig] int put_ClassStr([MarshalAs(UnmanagedType.BStr)] string s);
        [PreserveSig] int CreateStorage([MarshalAs(UnmanagedType.BStr)] string name, uint mode, uint flags, out IntPtr stg);
        [PreserveSig] int OpenStorage([MarshalAs(UnmanagedType.BStr)] string name, uint mode, uint flags, [MarshalAs(UnmanagedType.BStr)] string version, out IntPtr stg);
        [PreserveSig] int CreateStream([MarshalAs(UnmanagedType.BStr)] string name, uint mode, uint flags, out IntPtr stm);
        [PreserveSig] int OpenStream([MarshalAs(UnmanagedType.BStr)] string name, uint mode, uint flags, [MarshalAs(UnmanagedType.BStr)] string version, out IntPtr stm);
        [PreserveSig] int Commit(uint flags);
        [PreserveSig] int Revert();
        [PreserveSig] int get_FullFilePath([MarshalAs(UnmanagedType.BStr)] out string s);
        [PreserveSig] int CopyToNewStorageFile([MarshalAs(UnmanagedType.BStr)] string name, int failIfExists);
        [PreserveSig] int DestroyChildElement([MarshalAs(UnmanagedType.BStr)] string name);
    }

    public static class Runner
    {
        static readonly Guid IID_ISwxStorage = new Guid("194683E4-C55C-4EB8-9381-02BB091E1695");
        static readonly Guid IID_IStorage = new Guid("0000000B-0000-0000-C000-000000000046");

        static object Call(object o, string m, params object[] a)
        {
            return o.GetType().InvokeMember(m, BindingFlags.InvokeMethod, null, o, a);
        }

        static IntPtr QI(object o, Guid iid)
        {
            IntPtr unk = Marshal.GetIUnknownForObject(o), p;
            int hr = Marshal.QueryInterface(unk, ref iid, out p);
            Marshal.Release(unk);
            if (hr != 0) throw new Exception("QI failed 0x" + hr.ToString("X8"));
            return p;
        }

        public static string Run(string swxcf, string panelPath, string mode)
        {
            var log = new System.Text.StringBuilder();
            Action<string> L = s => log.AppendLine(s);
            object mgrObj = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("FE268358-B12F-44A0-8736-D409978EFAC5")));
            var mgr = (ISwxCompoundFileManager)mgrObj;
            L("SwxLocal created, ISwxCompoundFileManager ok");
            int h = mgr.InitializeCompoundFileManager(); L("InitializeCompoundFileManager -> 0x" + h.ToString("X8"));
            IntPtr pRoot = IntPtr.Zero;
            uint grf = 0x12;                                  // STGM_READWRITE | STGM_SHARE_EXCLUSIVE
            string dir = System.IO.Path.GetDirectoryName(swxcf), file = System.IO.Path.GetFileName(swxcf);
            var tries = new[] { new[] { "", swxcf }, new[] { dir, file }, new[] { "VJMCP", swxcf }, new[] { swxcf, "" } };
            foreach (var kn in tries)
            {
                h = mgr.OpenSwxCompoundFile(kn[0], kn[1], grf, 0, "", out pRoot);
                L("OpenSwxCompoundFile(key='" + kn[0] + "', name='" + kn[1] + "') -> 0x" + h.ToString("X8"));
                if (h == 0 && pRoot != IntPtr.Zero) break;
            }
            if (pRoot == IntPtr.Zero) return log.ToString();
            var root = (ISwxStorage)Marshal.GetObjectForIUnknown(pRoot);
            string fp; root.get_FullFilePath(out fp); L("root storage file: " + fp);
            ISwxStorage cur = root;
            foreach (var part in panelPath.Split('/'))
            {
                IntPtr p; h = cur.OpenStorage(part, grf, 0, "", out p);
                if (h != 0) { L("OpenStorage('" + part + "') -> 0x" + h.ToString("X8")); return log.ToString(); }
                cur = (ISwxStorage)Marshal.GetObjectForIUnknown(p);
            }
            object stg = cur;
            string nm; cur.get_Name(out nm); L("panel ISwxStorage opened: " + nm);
            object doc = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("451BDCD8-5851-4B24-B663-2A7CA707C9C5")));
            var td = (ITISDocument)doc; var ps = (IPersistStorage)doc;
            IntPtr pSwx = QI(stg, IID_ISwxStorage), pStg = QI(stg, IID_IStorage);
            int hr;
            if (mode.Contains("init"))
            {
                hr = td.Initialize(IntPtr.Zero, 0, 0, pSwx, IntPtr.Zero); L("ITISDocument.Initialize -> 0x" + hr.ToString("X8"));
            }
            if (mode.Contains("load"))
            {
                hr = ps.Load(pStg); L("IPersistStorage.Load(SwxStorage as IStorage) -> 0x" + hr.ToString("X8"));
            }
            hr = td.IsDirty(); L("IsDirty -> 0x" + hr.ToString("X8"));
            if (mode.Contains("save"))
            {
                hr = ps.Save(pStg, true); L("IPersistStorage.Save(same) -> 0x" + hr.ToString("X8"));
                hr = ps.SaveCompleted(IntPtr.Zero); L("SaveCompleted -> 0x" + hr.ToString("X8"));
                hr = td.Save(); L("ITISDocument.Save -> 0x" + hr.ToString("X8"));
                hr = td.Commit(); L("ITISDocument.Commit -> 0x" + hr.ToString("X8"));
            }
            L("panel Commit -> 0x" + cur.Commit(0).ToString("X8"));
            L("root Commit -> 0x" + root.Commit(0).ToString("X8"));
            try { td.Shutdown(0); } catch { }
            Marshal.Release(pSwx); Marshal.Release(pStg);
            Marshal.ReleaseComObject(doc); Marshal.ReleaseComObject(stg); Marshal.ReleaseComObject(root);
            return log.ToString();
        }
    }
}

