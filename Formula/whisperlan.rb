class Whisperlan < Formula
  desc "Terminal whispers for your LAN: group room + encrypted private 1-on-1s"
  homepage "https://github.com/Tarraf2020/whisperlan"
  url "https://github.com/Tarraf2020/whisperlan/archive/refs/tags/v1.5.1.tar.gz"
  sha256 "2633ca098abc547e3b4a83b17e9d8f0833e8fe53e167310a41fdba116efa0d2e"
  license "MIT"
  depends_on "python@3.13"

  def install
    inreplace "whisper.py", "#!/usr/bin/env python3",
              "#!#{formula_opt_bin("python@3.13")}/python3.13"
    bin.install "whisper.py" => "whisperlan"
    bin.install_symlink "whisperlan" => "whisper"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/whisperlan --version")
  end
end
