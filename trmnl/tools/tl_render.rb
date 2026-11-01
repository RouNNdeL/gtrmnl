# encoding: utf-8
# Render shared + layout markup with TRMNL's own Liquid (trmnl-liquid gem).
# This is the authoritative local renderer; render.py is a stand-in.
#   echo '{"src": "...", "ctx": {...}}' | LANG=C.UTF-8 ruby -E UTF-8 -W0 \
#     -I ext/trmnl-liquid/lib trmnl/tools/tl_render.rb
require "json"
require "trmnl/liquid"
input = JSON.parse($stdin.read)
env = TRMNL::Liquid.new
print Liquid::Template.parse(input["src"], environment: env).render(input["ctx"])
