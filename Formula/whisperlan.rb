class Whisperlan < Formula
  desc "Terminal whispers for your LAN: group room + encrypted private 1-on-1s"
  homepage "https://github.com/Tarraf2020/whisperlan"
  url "https://github.com/Tarraf2020/whisperlan/archive/refs/tags/v1.4.1.tar.gz"
  sha256 "ef815da847f1eb11d5d0c5a60480b6faa5ac1aab36ef1f0bf3b3ae36eb728bd0"
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
