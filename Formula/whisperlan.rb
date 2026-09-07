class Whisperlan < Formula
  desc "Terminal whispers for your LAN: group room + encrypted private 1-on-1s"
  homepage "https://github.com/Tarraf2020/whisperlan"
  url "https://github.com/Tarraf2020/whisperlan/archive/refs/tags/v1.5.0.tar.gz"
  sha256 "b3d8c9bc6857efb834f90d6017dbccb041db6d1131ef5a4129d53f9dc1001a94"
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
