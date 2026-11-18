import java.io.*; import java.nio.charset.StandardCharsets;
public final class UpdaterIpcExample {
  static String request(String id, String command) { return "{\"protocol_version\":1,\"type\":\"request\",\"request_id\":\""+id+"\",\"command\":\""+command+"\",\"params\":{}}"; }
  public static void main(String[] a) throws Exception {
    Process p = new ProcessBuilder(a[0], "serve", "--stdio", "--data-root", a[1]).start();
    try (BufferedWriter in = new BufferedWriter(new OutputStreamWriter(p.getOutputStream(), StandardCharsets.UTF_8)); BufferedReader out = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
      for (String[] q : new String[][] {{"1","initialize"},{"2","get_version"},{"3","get_status"},{"4","shutdown"}}) { in.write(request(q[0],q[1])); in.newLine(); in.flush(); String line=out.readLine(); if(line==null || !line.contains("\"success\":true")) throw new IOException("invalid IPC response: "+line); System.out.println(line); }
    }
    if (!p.waitFor(10, java.util.concurrent.TimeUnit.SECONDS) || p.exitValue()!=0) throw new IllegalStateException("Updater exit failed");
  }
}
