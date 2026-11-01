# encoding: utf-8
# Render markup with TRMNL's own Liquid (the trmnl-liquid gem), so local
# output matches the server: Ruby Liquid 5 semantics (integer division,
# split dropping trailing empty strings, ...), TRMNL's filters and its
# {% template %} tag.
#
# One job:  {"src": "...", "ctx": {...}}      -> prints the HTML
# Batch:    [{"src": ..., "ctx": ...}, ...]   -> prints a JSON array of HTML
#
#   LANG=C.UTF-8 ruby -E UTF-8 -W0 -I <trmnl-liquid>/lib trmnl/tools/tl_render.rb < job.json
require "json"
require "trmnl/liquid"

def render(job)
  env = TRMNL::Liquid.new
  Liquid::Template.parse(job["src"], environment: env).render(job["ctx"])
end

input = JSON.parse($stdin.read)
if input.is_a?(Array)
  print JSON.generate(input.map { |job| render(job) })
else
  print render(input)
end
